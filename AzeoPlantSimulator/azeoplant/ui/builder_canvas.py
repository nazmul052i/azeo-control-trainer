"""The display builder's canvas: the editable scene and its view.

The scene draws the grid, the selection chrome and grid-snapped moves,
and reports each drag gesture as one move batch for the undo stack.
The view adds the navigation the reference drawing tool has: Ctrl+wheel
zoom anchored under the pointer (25-300%), middle-button panning and a
fit-to-content command. ``export_mode`` suppresses every editing aid so
the same scene renders clean for PNG/SVG export.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath,
                           QPen, QTransform)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView

from .hmi import CANVAS, SELECT, Connector, HmiScene, Pipe

DESK = QColor("#C9CED5")          # the surface behind the sheet

TARGET_OK = QColor("#2E9E4F")
GUIDE = QColor("#00A3C4")


def _chip(p: QPainter, at: QPointF, text: str) -> None:
    """A small white readout chip - live coordinates and dimensions."""
    f = QFont()
    f.setPointSizeF(7.5)
    p.setFont(f)
    fm = p.fontMetrics()
    r = QRectF(at.x(), at.y(), fm.horizontalAdvance(text) + 10, 16)
    p.setPen(QPen(QColor("#9AA6B0"), 1))
    p.setBrush(QBrush(QColor(255, 255, 255, 235)))
    p.drawRoundedRect(r, 3, 3)
    p.setPen(QPen(QColor("#24407C")))
    p.drawText(r, Qt.AlignCenter, text)

GRID = 5.0

ZOOM_MIN, ZOOM_MAX = 0.25, 3.0


class BuilderScene(HmiScene):
    """The editable canvas: a light grid, dashed selection frames and
    grid-snapped moves. ``edit_mode`` makes every HmiItem draggable
    instead of clickable."""

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.edit_mode = True
        self.export_mode = False
        #: callback([(id, (x0, y0), (x1, y1))]) after a drag gesture
        self.on_moved = None
        self._press_pos: Dict[int, Tuple[float, float]] = {}
        #: live connector-drag feedback, owned by the builder: the
        #: routed preview polyline
        self.conn_preview: list | None = None
        self.conn_hover = None
        self.conn_hover_pt: QPointF | None = None
        #: smart-guide state while dragging
        self.guides: list = []
        self.drag_pos: Tuple[float, float] | None = None
        self._guide_lock = False
        #: hover connection ports (draw.io style), owned by the builder
        self.hover_item = None
        self.hover_port = None

    def drawBackground(self, p: QPainter, rect: QRectF) -> None:  # noqa: N802
        sheet_col = self.backgroundBrush().color()
        if self.export_mode:
            p.fillRect(rect, sheet_col)
            return
        # the sheet-on-desk look: darker surround, soft shadow, page
        p.fillRect(rect, DESK)
        sheet = self.sceneRect()
        p.fillRect(sheet.translated(3, 3), QColor(0, 0, 0, 34))
        p.fillRect(sheet, sheet_col)
        area = rect.intersected(sheet)
        p.setPen(QPen(QColor("#DDE1E6"), 0))
        step = 50
        x = int(area.left()) - int(area.left()) % step
        while x <= area.right():
            p.drawLine(x, area.top(), x, area.bottom())
            x += step
        y = int(area.top()) - int(area.top()) % step
        while y <= area.bottom():
            p.drawLine(area.left(), y, area.right(), y)
            y += step
        p.setPen(QPen(QColor("#AEB6BF"), 0))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sheet)

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:  # noqa: N802
        if self.export_mode:
            return
        # lines select as lines - a glow along the path with endpoint
        # grips, the way Visio and AutoCAD show them - and everything
        # else keeps the dashed frame
        for it in self.selectedItems():
            pts = None
            if isinstance(it, Connector):
                pts = list(it._pts)
            elif isinstance(it, Pipe):
                pts = [it.mapToScene(q) for q in it.pts]
            if pts and len(pts) >= 2:
                glow = QColor(SELECT)
                glow.setAlpha(90)
                p.setPen(QPen(glow, 6.0, Qt.SolidLine, Qt.RoundCap,
                              Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                for a, b in zip(pts, pts[1:]):
                    p.drawLine(a, b)
                p.setPen(QPen(SELECT, 1.2))
                p.setBrush(QBrush(QColor("#FFFFFF")))
                for q in (pts[0], pts[-1]):
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
            else:
                p.setPen(QPen(SELECT, 1.6, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(
                    it.sceneBoundingRect().adjusted(-3, -3, 3, 3))
        # bend diamonds on selected connectors
        p.setPen(QPen(SELECT, 1.2))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        for it in self.selectedItems():
            if isinstance(it, Connector):
                for b in it.bends:
                    p.drawPolygon([QPointF(b.x(), b.y() - 6),
                                   QPointF(b.x() + 6, b.y()),
                                   QPointF(b.x(), b.y() + 6),
                                   QPointF(b.x() - 6, b.y())])
        # smart guides and the live position readout while dragging
        if self.guides:
            p.setPen(QPen(GUIDE, 0, Qt.DashLine))
            for g in self.guides:
                if g[0] == "v":
                    p.drawLine(QPointF(g[1], g[2]), QPointF(g[1], g[3]))
                else:
                    p.drawLine(QPointF(g[2], g[1]), QPointF(g[3], g[1]))
        if self.drag_pos is not None:
            _chip(p, QPointF(self.drag_pos[0] + 16, self.drag_pos[1] + 20),
                  f"{self.drag_pos[0]:.0f}, {self.drag_pos[1]:.0f}")
        # hover connection ports on the symbol under the pointer
        if self.hover_item is not None:
            try:
                it = self.hover_item
                w = getattr(it, "_w", 60.0)
                h = getattr(it, "_h", 60.0)
                declared = getattr(it, "ports", None) or {}
                for side, fx, fy in (("L", 0.0, 0.5), ("R", 1.0, 0.5),
                                     ("T", 0.5, 0.0), ("B", 0.5, 1.0)):
                    fx, fy = declared.get(side, (fx, fy))
                    c = it.mapToScene(QPointF(-w / 2 + fx * w,
                                              -h / 2 + fy * h))
                    hot = side == self.hover_port
                    p.setPen(QPen(TARGET_OK if hot
                                  else QColor("#5B84B8"), 1.6))
                    p.setBrush(QBrush(TARGET_OK if hot
                                      else QColor("#FFFFFF")))
                    p.drawEllipse(c, 5.0 if hot else 4.0,
                                  5.0 if hot else 4.0)
            except RuntimeError:      # hovered item just deleted
                self.hover_item = self.hover_port = None
        # connector-drag preview and its candidate target
        if self.conn_hover is not None:
            p.setPen(QPen(TARGET_OK, 2.2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(
                self.conn_hover.sceneBoundingRect().adjusted(-3, -3, 3, 3))
        if self.conn_hover_pt is not None:
            p.setPen(QPen(TARGET_OK, 2.0))
            p.setBrush(QBrush(QColor("#FFFFFF")))
            p.drawEllipse(self.conn_hover_pt, 5.5, 5.5)
        if self.conn_preview:
            # the route as it will actually be drawn, pulled live
            pen = QPen(TARGET_OK, 1.8, Qt.DashLine)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for a, b in zip(self.conn_preview,
                            self.conn_preview[1:]):
                p.drawLine(a, b)

    SNAP_TOL = 6.0

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        super().mousePressEvent(ev)
        self._press_pos = {it._item_id: (it.pos().x(), it.pos().y())
                           for it in self.selectedItems()
                           if hasattr(it, "_item_id")}

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        super().mouseMoveEvent(ev)
        if not (ev.buttons() & Qt.LeftButton) or not self._press_pos:
            return
        grab = self.mouseGrabberItem()
        if grab is None or not hasattr(grab, "_item_id") \
                or isinstance(grab, Connector):
            return
        sel = [it for it in self.selectedItems()
               if hasattr(it, "_item_id") and not isinstance(it, Connector)]
        if not sel:
            return
        axis = None
        if ev.modifiers() & Qt.ShiftModifier:
            start = self._press_pos.get(grab._item_id)
            if start is not None:
                dx = grab.pos().x() - start[0]
                dy = grab.pos().y() - start[1]
                adj = (QPointF(0.0, -dy) if abs(dx) >= abs(dy)
                       else QPointF(-dx, 0.0))
                axis = "h" if abs(dx) >= abs(dy) else "v"
                if adj.x() or adj.y():
                    for it in sel:
                        it.setPos(it.pos() + adj)
        self.guides = []
        self._guide_lock = False
        if not (ev.modifiers() & Qt.AltModifier):
            self._smart_snap(grab, sel, axis)
        p = grab.pos()
        self.drag_pos = (p.x(), p.y())
        self.update()

    def _smart_snap(self, grab, sel, axis) -> None:
        """Magnetic alignment against other symbols' edges and centres,
        with a cyan guide naming the shared axis."""
        w = getattr(grab, "_w", 60.0)
        h = getattr(grab, "_h", 60.0)
        p = grab.pos()
        gx = (p.x() - w / 2, p.x(), p.x() + w / 2)
        gy = (p.y() - h / 2, p.y(), p.y() + h / 2)
        moving = {it._item_id for it in sel}
        best_dx = best_dy = None
        vline = hline = None
        for it in self.items():
            if (not hasattr(it, "_item_id") or it._item_id in moving
                    or isinstance(it, Connector)):
                continue
            ow = getattr(it, "_w", 60.0)
            oh = getattr(it, "_h", 60.0)
            op = it.pos()
            ox = (op.x() - ow / 2, op.x(), op.x() + ow / 2)
            oy = (op.y() - oh / 2, op.y(), op.y() + oh / 2)
            if axis != "h":
                for a in gx:
                    for bx in ox:
                        d = bx - a
                        if abs(d) <= self.SNAP_TOL and (
                                best_dx is None or abs(d) < abs(best_dx)):
                            best_dx = d
                            vline = ("v", bx, min(gy[0], oy[0]) - 12,
                                     max(gy[2], oy[2]) + 12)
            if axis != "v":
                for a in gy:
                    for by in oy:
                        d = by - a
                        if abs(d) <= self.SNAP_TOL and (
                                best_dy is None or abs(d) < abs(best_dy)):
                            best_dy = d
                            hline = ("h", by, min(gx[0], ox[0]) - 12,
                                     max(gx[2], ox[2]) + 12)
        adj = QPointF(best_dx or 0.0, best_dy or 0.0)
        if adj.x() or adj.y():
            for it in sel:
                it.setPos(it.pos() + adj)
        if best_dx is not None:
            self.guides.append(vline)
            self._guide_lock = True
        if best_dy is not None:
            self.guides.append(hline)
            self._guide_lock = True

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        super().mouseReleaseEvent(ev)
        moves = []
        for it in self.selectedItems():
            if not hasattr(it, "_item_id"):
                continue
            pos = it.pos()
            if self._guide_lock:
                # a guide snapped this exactly; the grid must not undo it
                x, y = pos.x(), pos.y()
            else:
                x = round(pos.x() / GRID) * GRID
                y = round(pos.y() / GRID) * GRID
                it.setPos(x, y)
            old = self._press_pos.get(it._item_id)
            if old is not None and (abs(old[0] - x) > 1e-9
                                    or abs(old[1] - y) > 1e-9):
                moves.append((it._item_id, old, (x, y)))
        self._press_pos = {}
        self.guides = []
        self.drag_pos = None
        self._guide_lock = False
        self.update()
        if moves and self.on_moved is not None:
            self.on_moved(moves)


class HandlesOverlay(QGraphicsItem):
    """Resize and rotation grips around the single selected item.

    A grip drag paints a translucent ghost only - the item is not
    touched until release, when ``on_commit(old, new)`` fires once with
    {'w','h','cx','cy','rot'} geometry dicts in scene pixels. Grip
    sizes divide by the view scale so they stay constant on screen.
    Resize grips honour the target's sizable dims and disappear on a
    rotated target; the rotation grip snaps to 15 degrees (Alt frees
    it, and Alt also bypasses the grid on resize; Shift frees a locked
    aspect)."""

    GRIP = 7.0

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(1000)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.target = None
        self.dims = set()
        self.keep_aspect = True
        self.view_scale = 1.0
        self.on_commit = None
        self._drag = None
        self._ghost = None
        self.hide()

    # ------------------------------------------------------------- target
    def set_target(self, item, dims, keep_aspect: bool) -> None:
        if self.target is not None:
            for sig in (self.target.xChanged, self.target.yChanged):
                try:
                    sig.disconnect(self._follow)
                except (RuntimeError, TypeError):
                    pass
        self.prepareGeometryChange()
        self.target = item
        self.dims = set(dims)
        self.keep_aspect = keep_aspect
        self._drag = self._ghost = None
        if item is None:
            self.hide()
            return
        for sig in (item.xChanged, item.yChanged):
            sig.connect(self._follow)
        self.show()
        self.update()

    def _follow(self) -> None:
        self.prepareGeometryChange()
        self.update()

    def set_view_scale(self, s: float) -> None:
        self.prepareGeometryChange()
        self.view_scale = max(float(s), 1e-6)
        self.update()

    # ----------------------------------------------------------- geometry
    def _frame(self) -> QRectF:
        it = self.target
        w = getattr(it, "_w", 60.0)
        h = getattr(it, "_h", 60.0)
        p = it.pos()
        return QRectF(p.x() - w / 2, p.y() - h / 2, w, h)

    def _grips(self):
        if self.target is None:
            return []
        f = self._frame()
        out = []
        if abs(self.target.rotation()) < 1e-6:
            if "w" in self.dims:
                out += [("L", QPointF(f.left(), f.center().y())),
                        ("R", QPointF(f.right(), f.center().y()))]
            if "h" in self.dims:
                out += [("T", QPointF(f.center().x(), f.top())),
                        ("B", QPointF(f.center().x(), f.bottom()))]
            if "w" in self.dims and "h" in self.dims:
                out += [("TL", f.topLeft()), ("TR", f.topRight()),
                        ("BL", f.bottomLeft()), ("BR", f.bottomRight())]
        out.append(("ROT", QPointF(f.center().x(),
                                   f.top() - 22.0 / self.view_scale)))
        return out

    def boundingRect(self) -> QRectF:
        if self.target is None:
            return QRectF()
        m = 34.0 / self.view_scale
        r = self._frame().adjusted(-m, -m, m, m)
        if self._ghost is not None:
            g = self._ghost
            d = math.hypot(g["w"], g["h"]) / 2 + m
            r = r.united(QRectF(g["cx"] - d, g["cy"] - d, 2 * d, 2 * d))
        return r

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        g = (self.GRIP + 2.0) / self.view_scale
        for _kind, c in self._grips():
            path.addEllipse(c, g, g)
        return path

    # -------------------------------------------------------------- mouse
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        g = (self.GRIP + 3.0) / self.view_scale
        for kind, c in self._grips():
            d = ev.pos() - c
            if abs(d.x()) <= g and abs(d.y()) <= g:
                f = self._frame()
                self._drag = {"kind": kind, "start": ev.pos(),
                              "orig": (f.width(), f.height(),
                                       f.center().x(), f.center().y(),
                                       self.target.rotation())}
                ev.accept()
                return
        ev.ignore()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag is None:
            return
        self.prepareGeometryChange()
        if self._drag["kind"] == "ROT":
            self._ghost = self._rot_ghost(ev.pos(), ev.modifiers())
        else:
            self._ghost = self._resize_ghost(ev.pos(), ev.modifiers())
        self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if self._drag is None:
            return
        ghost, drag = self._ghost, self._drag
        self._drag = self._ghost = None
        self.prepareGeometryChange()
        self.update()
        if ghost is None:
            return
        w0, h0, cx0, cy0, r0 = drag["orig"]
        old = {"w": w0, "h": h0, "cx": cx0, "cy": cy0, "rot": r0}
        if self.on_commit is not None and ghost != old:
            self.on_commit(old, dict(ghost))

    def _resize_ghost(self, pos, mods) -> dict:
        d = pos - self._drag["start"]
        kind = self._drag["kind"]
        w0, h0, cx0, cy0, r0 = self._drag["orig"]
        dw = d.x() if "R" in kind else -d.x() if "L" in kind else 0.0
        dh = d.y() if "B" in kind else -d.y() if "T" in kind else 0.0
        if "w" not in self.dims:
            dw = 0.0
        if "h" not in self.dims:
            dh = 0.0
        w1 = max(12.0, w0 + dw)
        h1 = max(12.0, h0 + dh)
        aspect = self.keep_aspect and not (mods & Qt.ShiftModifier)
        if aspect and "w" in self.dims and "h" in self.dims:
            if kind in ("L", "R"):
                h1 = h0 * w1 / w0
            elif kind in ("T", "B"):
                w1 = w0 * h1 / h0
            else:
                s = max(w1 / w0, h1 / h0)
                w1, h1 = w0 * s, h0 * s
        if not (mods & Qt.AltModifier):
            w1 = max(GRID, round(w1 / GRID) * GRID)
            h1 = max(GRID, round(h1 / GRID) * GRID)
        if mods & Qt.ControlModifier:
            # resize about the centre: growth doubles, centre stays
            w1 = max(12.0, w0 + 2 * (w1 - w0))
            h1 = max(12.0, h0 + 2 * (h1 - h0))
            return {"w": w1, "h": h1, "cx": cx0, "cy": cy0, "rot": r0}
        cx1 = cx0 + (w1 - w0) / 2 * (1 if "R" in kind
                                     else -1 if "L" in kind else 0)
        cy1 = cy0 + (h1 - h0) / 2 * (1 if "B" in kind
                                     else -1 if "T" in kind else 0)
        return {"w": w1, "h": h1, "cx": cx1, "cy": cy1, "rot": r0}

    def _rot_ghost(self, pos, mods) -> dict:
        w0, h0, cx0, cy0, r0 = self._drag["orig"]
        a1 = math.degrees(math.atan2(pos.y() - cy0, pos.x() - cx0))
        s = self._drag["start"]
        a0 = math.degrees(math.atan2(s.y() - cy0, s.x() - cx0))
        rot = (r0 + a1 - a0) % 360.0
        if not (mods & Qt.AltModifier):
            rot = (round(rot / 15.0) * 15.0) % 360.0
        return {"w": w0, "h": h0, "cx": cx0, "cy": cy0, "rot": rot}

    # -------------------------------------------------------------- paint
    def paint(self, p: QPainter, opt, widget=None) -> None:
        if self.target is None:
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        g = self.GRIP / self.view_scale
        pen = QPen(SELECT, 1.2 / self.view_scale)
        p.setPen(pen)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        f = self._frame()
        for kind, c in self._grips():
            if kind == "ROT":
                p.drawLine(QPointF(f.center().x(), f.top()), c)
                p.drawEllipse(c, g * 0.75, g * 0.75)
            else:
                p.drawRect(QRectF(c.x() - g * 0.6, c.y() - g * 0.6,
                                  g * 1.2, g * 1.2))
        if self._ghost is not None:
            gh = self._ghost
            p.save()
            p.translate(gh["cx"], gh["cy"])
            p.rotate(gh["rot"])
            p.setPen(QPen(SELECT, 1.4 / self.view_scale, Qt.DashLine))
            p.setBrush(QBrush(QColor(112, 48, 160, 26)))
            p.drawRect(QRectF(-gh["w"] / 2, -gh["h"] / 2,
                              gh["w"], gh["h"]))
            p.restore()
            text = (f'{gh["rot"]:.0f}°'
                    if self._drag is not None
                    and self._drag["kind"] == "ROT"
                    else f'{gh["w"]:.0f} × {gh["h"]:.0f}')
            _chip(p, QPointF(gh["cx"] + gh["w"] / 2 + 8,
                             gh["cy"] + gh["h"] / 2 + 8), text)


class BuilderView(QGraphicsView):
    """Canvas navigation: Ctrl+wheel zoom under the pointer,
    middle-button pan, fit-to-content."""

    zoomChanged = Signal(float)

    def __init__(self, scene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self._pan_from = None

    # ------------------------------------------------------------- zooming
    def _scale_to(self, factor: float) -> None:
        cur = self.transform().m11()
        f = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor)) / cur
        if abs(f - 1.0) > 1e-9:
            self.scale(f, f)
            self.zoomChanged.emit(self.transform().m11())

    def wheelEvent(self, ev) -> None:  # noqa: N802
        if ev.modifiers() & Qt.ControlModifier:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self._scale_to(1.1 ** (ev.angleDelta().y() / 120.0))
            ev.accept()
            return
        super().wheelEvent(ev)

    def zoom_step(self, direction: int) -> None:
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self._scale_to(1.1 ** direction)

    def fit(self) -> None:
        """Fit the selection if there is one, else everything."""
        scene = self.scene()
        rect = QRectF()
        for it in scene.selectedItems():
            rect = rect.united(it.sceneBoundingRect())
        if rect.isNull():
            rect = scene.itemsBoundingRect()
        if rect.isNull():
            rect = scene.sceneRect()
        rect = rect.adjusted(-40, -40, 40, 40)
        self.fitInView(rect, Qt.KeepAspectRatio)
        if self.transform().m11() > 2.0:
            self.setTransform(QTransform().scale(2.0, 2.0))
            self.centerOn(rect.center())
        self.zoomChanged.emit(self.transform().m11())

    # ------------------------------------------------------------- panning
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MiddleButton:
            self._pan_from = ev.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._pan_from is not None:
            d = ev.position().toPoint() - self._pan_from
            self._pan_from = ev.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - d.y())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MiddleButton and self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)
