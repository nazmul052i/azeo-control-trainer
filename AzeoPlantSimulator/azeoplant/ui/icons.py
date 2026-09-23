"""The application glyph set, drawn programmatically.

One consistent vector icon family for every menu, toolbar and band in
the product - no image assets to ship or go stale, crisp at any DPI.
Chrome glyphs are the navy ink of the light engineering theme; the
simulation-state glyphs keep their semantic colours (run green, freeze
amber, clear red, alarm bell amber) so state reads at a glance.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)

INK = QColor("#33517F")
FILL = QColor("#C7D4E6")


def make_icon(kind: str, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    if size != 20:
        p.scale(size / 20.0, size / 20.0)
    pen = QPen(INK, 1.5)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    def poly(*pts, brush=False, colour=None):
        pg = QPolygonF([QPointF(x, y) for x, y in pts])
        if brush:
            p.setBrush(QBrush(colour or INK))
            if colour is not None:
                p.setPen(Qt.NoPen)
        p.drawPolygon(pg)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def rect(x, y, w, h, fill=None):
        if fill is not None:
            p.setBrush(QBrush(fill))
        p.drawRect(QRectF(x, y, w, h))
        p.setBrush(Qt.NoBrush)

    def path(*pts, close=False, start=None):
        pt = QPainterPath(QPointF(*(start or pts[0])))
        for x, y in (pts if start else pts[1:]):
            pt.lineTo(x, y)
        if close:
            pt.closeSubpath()
        p.drawPath(pt)

    # ------------------------------------------------- simulation state
    if kind == "run":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2F6B3A"))
        p.drawPolygon(QPolygonF([QPointF(5.5, 3.5), QPointF(16.5, 10),
                                 QPointF(5.5, 16.5)]))
    elif kind == "freeze":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#B26A00"))
        p.drawRect(QRectF(5, 3.5, 3.6, 13))
        p.drawRect(QRectF(11.4, 3.5, 3.6, 13))
    elif kind == "step":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3A5A8C"))
        p.drawPolygon(QPolygonF([QPointF(4, 3.5), QPointF(13, 10),
                                 QPointF(4, 16.5)]))
        p.drawRect(QRectF(14.2, 3.5, 2.4, 13))
    elif kind == "clear":
        pen2 = QPen(QColor("#8A2525"), 2.6)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        line(5, 5, 15, 15)
        line(15, 5, 5, 15)
    elif kind == "alarm":
        p.setPen(QPen(QColor("#7A5A00"), 1.6))
        p.setBrush(QColor("#E8A317"))
        pt = QPainterPath(QPointF(4.5, 13))
        pt.cubicTo(4.5, 6.5, 7, 4, 10, 4)
        pt.cubicTo(13, 4, 15.5, 6.5, 15.5, 13)
        pt.closeSubpath()
        p.drawPath(pt)
        line(3, 13.5, 17, 13.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#7A5A00"))
        p.drawEllipse(QPointF(10, 16), 1.8, 1.8)
    # ------------------------------------------------------- navigation
    elif kind == "home":
        path((3, 10), (10, 3.5), (17, 10), start=(3, 10))
        path((5, 9.5), (5, 16.5), (15, 16.5), (15, 9.5), start=(5, 9.5))
        rect(8.6, 11.5, 2.8, 5)
    elif kind in ("back", "forward"):
        if kind == "forward":
            p.translate(20, 0)
            p.scale(-1, 1)
        line(15.5, 10, 5, 10)
        poly((3.4, 10), (8, 6), (8, 14), brush=True)
    elif kind == "search":
        p.drawEllipse(QRectF(3.5, 3.5, 9.5, 9.5))
        line(11.7, 11.7, 16.5, 16.5)
    elif kind == "displays":
        rect(3, 3, 6.4, 6.4)
        rect(10.6, 3, 6.4, 6.4)
        rect(3, 10.6, 6.4, 6.4)
        rect(10.6, 10.6, 6.4, 6.4, FILL)
    elif kind == "table":
        rect(3, 4, 14, 12)
        line(3, 8, 17, 8)
        line(3, 12, 17, 12)
        line(8, 4, 8, 16)
    elif kind == "chart":
        line(3.5, 3.5, 3.5, 16.5)
        line(3.5, 16.5, 16.5, 16.5)
        path((5.5, 13), (9, 8.5), (11.5, 11), (16, 5.5),
             start=(5.5, 13))
    elif kind == "builder":
        # pencil over a small sheet
        rect(3, 5, 9, 11.5)
        p.save()
        p.translate(13.4, 9.6)
        p.rotate(45)
        rect(-1.7, -6.4, 3.4, 9.6, FILL)
        poly((-1.7, 3.2), (1.7, 3.2), (0, 6.2), brush=True)
        p.restore()
    elif kind == "gauge":
        p.drawArc(QRectF(3.5, 4.5, 13, 13), 0, 180 * 16)
        line(10, 11, 14, 7)
        p.setBrush(QBrush(INK))
        p.drawEllipse(QPointF(10, 11), 1.4, 1.4)
    elif kind == "settings":
        p.drawEllipse(QRectF(6.6, 6.6, 6.8, 6.8))
        for ang in range(0, 360, 45):
            p.save()
            p.translate(10, 10)
            p.rotate(ang)
            p.drawLine(QPointF(0, -8.4), QPointF(0, -6.2))
            p.restore()
    elif kind == "forces":
        line(10, 3, 10, 12)
        poly((10, 16.6), (6.4, 11.6), (13.6, 11.6), brush=True)
        line(4, 17, 16, 17)
    elif kind == "loop":
        p.drawArc(QRectF(4, 4, 12, 12), 40 * 16, 280 * 16)
        poly((15.9, 5.4), (12.3, 5.2), (14.8, 8.6), brush=True)
    # ---------------------------------------------------------- editing
    elif kind == "new":
        path((11, 3), (15, 7), (15, 17), (5, 17), start=(5, 3),
             close=True)
        line(11, 3, 11, 7)
        line(11, 7, 15, 7)
    elif kind == "open":
        path((8, 6), (10, 8), (17, 8), (17, 16), (3, 16), start=(3, 6),
             close=True)
    elif kind == "save":
        line(10, 3, 10, 11)
        poly((10, 13), (7, 9.5), (13, 9.5), brush=True)
        path((4, 16), (16, 16), (16, 12), start=(4, 12))
    elif kind in ("undo", "redo"):
        if kind == "redo":
            p.translate(20, 0)
            p.scale(-1, 1)
        pt = QPainterPath(QPointF(5, 8))
        pt.cubicTo(8, 4.5, 13, 4.5, 15.5, 8.5)
        pt.cubicTo(17.2, 11.5, 15.5, 15, 11, 15.5)
        p.drawPath(pt)
        poly((5, 8), (4, 3.5), (9.5, 5.5), brush=True)
    elif kind == "export":
        path((4, 9), (4, 17), (16, 17), (16, 9), (14, 9), start=(6, 9))
        line(10, 12, 10, 4.5)
        poly((10, 2.8), (7.3, 6), (12.7, 6), brush=True)
    elif kind == "delete":
        line(5, 6, 15, 6)
        line(8, 6, 8, 4)
        line(8, 4, 12, 4)
        line(12, 4, 12, 6)
        path((6.8, 16.5), (13.2, 16.5), (14, 6), start=(6, 6))
        line(8.6, 8.5, 8.9, 14)
        line(11.4, 8.5, 11.1, 14)
    # -------------------------------------------------- builder drawing
    elif kind == "connector":
        rect(3, 3, 5, 5)
        rect(12, 12, 5, 5)
        line(5.5, 8, 5.5, 14.5)
        line(5.5, 14.5, 10.5, 14.5)
    elif kind == "pipe":
        line(3, 15, 8.5, 15)
        line(8.5, 15, 8.5, 5.5)
        line(8.5, 5.5, 15, 5.5)
        poly((16.8, 5.5), (13.5, 3.8), (13.5, 7.2), brush=True)
    elif kind.startswith("al_"):
        side = kind[3:]
        if side in ("l", "c", "r"):
            x = {"l": 4, "c": 10, "r": 16}[side]
            line(x, 3, x, 17)
            x1 = {"l": 5.5, "c": 5, "r": 4.5}[side]
            x2 = {"l": 5.5, "c": 7, "r": 8.5}[side]
            rect(x1, 4.5, 10, 4, FILL)
            rect(x2, 11.5, 6, 4, FILL)
        else:
            y = {"t": 4, "m": 10, "b": 16}[side]
            line(3, y, 17, y)
            y1 = {"t": 5.5, "m": 5, "b": 4.5}[side]
            y2 = {"t": 5.5, "m": 7, "b": 8.5}[side]
            rect(4.5, y1, 4, 10, FILL)
            rect(11.5, y2, 4, 6, FILL)
    elif kind == "dist_h":
        for x in (3.5, 9, 14.5):
            rect(x, 5, 2.5, 10, FILL)
    elif kind == "dist_v":
        for y in (3.5, 9, 14.5):
            rect(5, y, 10, 2.5, FILL)
    elif kind in ("rot_l", "rot_r"):
        if kind == "rot_r":
            p.translate(20, 0)
            p.scale(-1, 1)
        p.drawArc(QRectF(4.5, 4.5, 11, 11), 30 * 16, 265 * 16)
        poly((5.5, 5.8), (3.2, 9.2), (8.6, 8.9), brush=True)
    elif kind == "flip":
        p.setPen(QPen(INK, 1.2, Qt.DashLine))
        line(10, 2.5, 10, 17.5)
        p.setPen(pen)
        poly((7.5, 5.5), (7.5, 14.5), (3, 14.5), brush=True)
        poly((12.5, 5.5), (12.5, 14.5), (17, 14.5))
    elif kind == "front":
        rect(3.5, 7.5, 9, 9)
        rect(7.5, 3.5, 9, 9, FILL)
    elif kind == "back_z":
        rect(7.5, 3.5, 9, 9)
        rect(3.5, 7.5, 9, 9, FILL)
    elif kind == "line_align":
        rect(2.5, 7.5, 4.5, 4.5)
        rect(13, 7.5, 4.5, 4.5)
        line(7, 9.75, 13, 9.75)
    elif kind == "fit":
        for x, y, dx, dy in ((3, 3, 1, 1), (17, 3, -1, 1),
                             (3, 17, 1, -1), (17, 17, -1, -1)):
            line(x, y, x + 4 * dx, y)
            line(x, y, x, y + 4 * dy)
    elif kind in ("zoom_in", "zoom_out"):
        p.drawEllipse(QRectF(3.5, 3.5, 9.5, 9.5))
        line(11.7, 11.7, 16.5, 16.5)
        line(6, 8.25, 10.5, 8.25)
        if kind == "zoom_in":
            line(8.25, 6, 8.25, 10.5)
    p.end()
    return QIcon(pm)
