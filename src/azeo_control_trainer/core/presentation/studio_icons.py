"""Shared DPI-safe vector icons used by Azeo engineering applications."""

# The compact painter routines use l/r/t/b as geometric edge names.
# ruff: noqa: E741

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication


from .brand import AUTHORING_BLUE


def studio_icon(name: str, size: int = 20, color: str | None = None) -> QIcon:
    """One icon vocabulary across engineering ribbons, menus and trees.

    Names the shared icon set knows are painted in its colour-with-depth style
    (the colour argument then only matters for the line style); the painter
    routines below remain for the few names it does not know yet.
    """
    return draw_icon(name, size, color or AUTHORING_BLUE)


def draw_icon(name: str, size: int, color: str) -> QIcon:
    """Draw a crisp vector icon using QPainter primitives."""
    from .icon_set import has_icon, make_icon
    if has_icon(name):
        return make_icon(name, size, colour=color)
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    real = int(size * dpr)
    px = QPixmap(real, real)
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    pen = QPen(c, max(1.2, size / 12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = size  # logical size
    m = s * 0.18  # margin

    _DRAW_MAP.get(name, _draw_fallback)(p, s, m, c, pen)

    p.end()
    return QIcon(px)


def _draw_fallback(p, s, m, c, pen):
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    r = s * 0.2
    p.drawEllipse(QPointF(s / 2, s / 2), r, r)


def _draw_new(p, s, m, c, pen):
    l, t, r, b = m, m, s - m, s - m
    fold = (r - l) * 0.3
    path = QPainterPath()
    path.moveTo(l, t)
    path.lineTo(r - fold, t)
    path.lineTo(r, t + fold)
    path.lineTo(r, b)
    path.lineTo(l, b)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(r - fold, t), QPointF(r - fold, t + fold))
    p.drawLine(QPointF(r - fold, t + fold), QPointF(r, t + fold))


def _draw_open(p, s, m, c, pen):
    l, t, r, b = m, m + s * 0.08, s - m, s - m
    tab_w = (r - l) * 0.35
    tab_h = (b - t) * 0.15
    path = QPainterPath()
    path.moveTo(l, t + tab_h)
    path.lineTo(l, b)
    path.lineTo(r, b)
    path.lineTo(r, t + tab_h)
    path.lineTo(l + tab_w + tab_h, t + tab_h)
    path.lineTo(l + tab_w, t)
    path.lineTo(l, t)
    path.closeSubpath()
    p.drawPath(path)


def _draw_save(p, s, m, c, pen):
    l, t, r, b = m, m, s - m, s - m
    p.drawRect(QRectF(l, t, r - l, b - t))
    lw = (r - l) * 0.6
    p.drawRect(QRectF(s / 2 - lw / 2, b - (b - t) * 0.4, lw, (b - t) * 0.35))
    sw = (r - l) * 0.4
    p.drawRect(QRectF(s / 2 - sw / 2 + sw * 0.15, t, sw, (b - t) * 0.3))


def _draw_undo(p, s, m, c, pen):
    cx, cy = s / 2, s / 2
    r = s * 0.3
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 150)
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 150, -240)
    p.drawPath(path)
    end = path.currentPosition()
    aw = s * 0.12
    p.drawLine(end, QPointF(end.x() - aw, end.y() - aw * 0.3))
    p.drawLine(end, QPointF(end.x() + aw * 0.1, end.y() - aw))


def _draw_redo(p, s, m, c, pen):
    cx, cy = s / 2, s / 2
    r = s * 0.3
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 30)
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 30, 240)
    p.drawPath(path)
    end = path.currentPosition()
    aw = s * 0.12
    p.drawLine(end, QPointF(end.x() + aw, end.y() - aw * 0.3))
    p.drawLine(end, QPointF(end.x() - aw * 0.1, end.y() - aw))


def _draw_compile(p, s, m, c, pen):
    cx, cy = s / 2, s / 2
    r_out, r_in = s * 0.38, s * 0.22
    teeth = 8
    path = QPainterPath()
    for i in range(teeth):
        a1 = math.radians(i * 360 / teeth - 12)
        a2 = math.radians(i * 360 / teeth + 12)
        a3 = math.radians(i * 360 / teeth + 360 / teeth / 2 - 8)
        a4 = math.radians(i * 360 / teeth + 360 / teeth / 2 + 8)
        if i == 0:
            path.moveTo(cx + r_out * math.cos(a1), cy + r_out * math.sin(a1))
        else:
            path.lineTo(cx + r_out * math.cos(a1), cy + r_out * math.sin(a1))
        path.lineTo(cx + r_out * math.cos(a2), cy + r_out * math.sin(a2))
        path.lineTo(cx + r_in * math.cos(a3), cy + r_in * math.sin(a3))
        path.lineTo(cx + r_in * math.cos(a4), cy + r_in * math.sin(a4))
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QPointF(cx, cy), s * 0.09, s * 0.09)


def _draw_download(p, s, m, c, pen):
    l = m + s * 0.05
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    poly = QPolygonF([QPointF(l, m), QPointF(s - m, s / 2), QPointF(l, s - m)])
    p.drawPolygon(poly)


def _draw_deactivate(p, s, m, c, pen):
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    inset = s * 0.25
    p.drawRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset))


def _draw_zoom_in(p, s, m, c, pen):
    cx, cy = s * 0.42, s * 0.42
    r = s * 0.25
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx + r * 0.7, cy + r * 0.7), QPointF(s - m, s - m))
    cr = r * 0.55
    p.drawLine(QPointF(cx - cr, cy), QPointF(cx + cr, cy))
    p.drawLine(QPointF(cx, cy - cr), QPointF(cx, cy + cr))


def _draw_zoom_out(p, s, m, c, pen):
    cx, cy = s * 0.42, s * 0.42
    r = s * 0.25
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx + r * 0.7, cy + r * 0.7), QPointF(s - m, s - m))
    cr = r * 0.55
    p.drawLine(QPointF(cx - cr, cy), QPointF(cx + cr, cy))


def _draw_zoom_fit(p, s, m, c, pen):
    d = s * 0.28
    l, t, r, b = m, m, s - m, s - m
    for x1, y1, x2, y2, x3, y3 in [
        (l, t + d, l, t, l + d, t),
        (r - d, t, r, t, r, t + d),
        (r, b - d, r, b, r - d, b),
        (l + d, b, l, b, l, b - d),
    ]:
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.drawLine(QPointF(x2, y2), QPointF(x3, y3))


def _draw_comment(p, s, m, c, pen):
    l, t, r, b = m, m, s - m, s - m * 1.6
    p.drawRoundedRect(QRectF(l, t, r - l, b - t), 2, 2)
    tx = l + (r - l) * 0.25
    p.drawLine(QPointF(tx, b), QPointF(tx - s * 0.06, s - m))
    p.drawLine(QPointF(tx, b), QPointF(tx + s * 0.12, b))


def _draw_values(p, s, m, c, pen):
    y1, y2, y3 = s * 0.28, s * 0.5, s * 0.72
    l, r = m, s - m
    p.drawLine(QPointF(l, y1), QPointF(r, y1))
    p.drawLine(QPointF(l, y2), QPointF(r, y2))
    p.drawLine(QPointF(l, y3), QPointF(r, y3))
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    dr = s * 0.04
    for y in (y1, y2, y3):
        p.drawEllipse(QPointF(l - s * 0.01, y), dr, dr)


def _draw_exec_order(p, s, m, c, pen):
    f = QFont("Segoe UI", int(s * 0.5))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "#")


def _draw_procedure(p, s, m, c, pen):
    # A condition feeding two steps stays legible at menu size without a font glyph.
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.5, s * 0.10), QPointF(s * 0.70, s * 0.30),
        QPointF(s * 0.5, s * 0.50), QPointF(s * 0.30, s * 0.30),
    ]))
    p.drawLine(QPointF(s * 0.5, s * 0.50), QPointF(s * 0.5, s * 0.61))
    p.drawLine(QPointF(s * 0.24, s * 0.61), QPointF(s * 0.76, s * 0.61))
    for x in (0.24, 0.76):
        p.drawLine(QPointF(s * x, s * 0.61), QPointF(s * x, s * 0.72))
        p.drawRect(QRectF(s * (x - 0.12), s * 0.72, s * 0.24, s * 0.18))


def _draw_align(p, s, m, c, pen):
    l = m + s * 0.05
    p.drawLine(QPointF(l, m), QPointF(l, s - m))
    p.drawLine(QPointF(l, s * 0.3), QPointF(s * 0.7, s * 0.3))
    p.drawLine(QPointF(l, s * 0.5), QPointF(s * 0.55, s * 0.5))
    p.drawLine(QPointF(l, s * 0.7), QPointF(s * 0.8, s * 0.7))


def _draw_auto(p, s, m, c, pen):
    rw, rh = s * 0.25, s * 0.2
    gap = s * 0.08
    x1, x2 = m, s / 2 + gap / 2
    y1, y2 = m + s * 0.05, s / 2 + gap / 2
    for x, y in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
        p.drawRect(QRectF(x, y, rw, rh))
    p.drawLine(QPointF(x1 + rw, y1 + rh / 2), QPointF(x2, y1 + rh / 2))


def _draw_compare(p, s, m, c, pen):
    cy = s / 2
    p.drawLine(QPointF(m, cy), QPointF(s - m, cy))
    aw = s * 0.12
    p.drawLine(QPointF(m, cy), QPointF(m + aw, cy - aw))
    p.drawLine(QPointF(m, cy), QPointF(m + aw, cy + aw))
    p.drawLine(QPointF(s - m, cy), QPointF(s - m - aw, cy - aw))
    p.drawLine(QPointF(s - m, cy), QPointF(s - m - aw, cy + aw))


def _draw_history(p, s, m, c, pen):
    cx, cy = s / 2, s / 2
    r = s * 0.33
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx, cy), QPointF(cx, cy - r * 0.7))
    p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.55, cy + r * 0.2))


def _draw_templates(p, s, m, c, pen):
    off = s * 0.08
    l, t, r, b = m + off, m, s - m, s - m - off
    p.drawRect(QRectF(l, t, r - l, b - t))
    p.drawRect(QRectF(m, m + off, r - l, b - t))


def _draw_search(p, s, m, c, pen):
    cx, cy = s * 0.42, s * 0.42
    r = s * 0.25
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx + r * 0.7, cy + r * 0.7), QPointF(s - m, s - m))


def _draw_datalog(p, s, m, c, pen):
    l, b = m, s - m
    bw = s * 0.14
    gap = s * 0.05
    heights = [0.4, 0.7, 0.5, 0.85]
    p.setBrush(c)
    for i, h in enumerate(heights):
        x = l + i * (bw + gap)
        ht = (s - 2 * m) * h
        p.drawRect(QRectF(x, b - ht, bw, ht))
    p.setBrush(Qt.NoBrush)


def _draw_connect(p, s, m, c, pen):
    cy = s / 2
    p.drawLine(QPointF(m, cy), QPointF(s - m, cy))
    aw = s * 0.15
    p.drawLine(QPointF(s - m, cy), QPointF(s - m - aw, cy - aw))
    p.drawLine(QPointF(s - m, cy), QPointF(s - m - aw, cy + aw))


def _draw_disconnect(p, s, m, c, pen):
    p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
    p.drawLine(QPointF(s - m, m), QPointF(m, s - m))


def _draw_io_config(p, s, m, c, pen):
    _draw_compile(p, s, m, c, pen)


def _draw_properties(p, s, m, c, pen):
    cx, cy = s / 2, s / 2
    r = s * 0.33
    p.drawEllipse(QPointF(cx, cy), r, r)
    f = QFont("Segoe UI", int(s * 0.35))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, s * 0.08, s, s), Qt.AlignCenter, "i")


def _draw_status(p, s, m, c, pen):
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.28, s * 0.28)


def _draw_diagnostics(p, s, m, c, pen):
    y = s / 2
    pts = [
        QPointF(m, y),
        QPointF(s * 0.3, y),
        QPointF(s * 0.38, m + s * 0.05),
        QPointF(s * 0.46, s - m - s * 0.05),
        QPointF(s * 0.54, m + s * 0.05),
        QPointF(s * 0.62, y),
        QPointF(s - m, y),
    ]
    for i in range(len(pts) - 1):
        p.drawLine(pts[i], pts[i + 1])


def _draw_upload(p, s, m, c, pen):
    cx = s / 2
    p.drawLine(QPointF(cx, m), QPointF(cx, s - m))
    aw = s * 0.15
    p.drawLine(QPointF(cx, m), QPointF(cx - aw, m + aw))
    p.drawLine(QPointF(cx, m), QPointF(cx + aw, m + aw))


def _draw_checkpoint(p, s, m, c, pen):
    l = m + s * 0.15
    p.drawLine(QPointF(l, m), QPointF(l, s - m))
    p.setBrush(c)
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(l, m + s * 0.05),
                QPointF(s - m, s * 0.3),
                QPointF(l, s * 0.42),
            ]
        )
    )


def _draw_restore(p, s, m, c, pen):
    _draw_undo(p, s, m, c, pen)


def _draw_simulator(p, s, m, c, pen):
    _draw_download(p, s, m, c, pen)


def _draw_presets(p, s, m, c, pen):
    for y in (s * 0.3, s * 0.5, s * 0.7):
        p.drawLine(QPointF(m, y), QPointF(s - m, y))


def _draw_cut(p, s, m, c, pen):
    """Scissors."""
    p.drawLine(QPointF(m, m), QPointF(s - m * 1.5, s - m * 1.9))
    p.drawLine(QPointF(s - m, m), QPointF(m * 1.5, s - m * 1.9))
    r = s * 0.1
    p.drawEllipse(QPointF(m * 1.35, s - m - r * 0.4), r, r)
    p.drawEllipse(QPointF(s - m * 1.35, s - m - r * 0.4), r, r)


def _draw_copy(p, s, m, c, pen):
    """Two offset sheets."""
    off = s * 0.16
    w = s - 2 * m - off
    p.drawRect(QRectF(m, m, w, w))
    p.drawRect(QRectF(m + off, m + off, w, w))


def _draw_paste(p, s, m, c, pen):
    """Clipboard with a clip."""
    l, t, r, b = m, m + s * 0.12, s - m, s - m
    p.drawRect(QRectF(l, t, r - l, b - t))
    cw = (r - l) * 0.44
    p.drawRect(QRectF(s / 2 - cw / 2, m, cw, s * 0.2))


def _draw_delete(p, s, m, c, pen):
    """Waste bin."""
    l, r = m + s * 0.06, s - m - s * 0.06
    top = m + s * 0.18
    p.drawLine(QPointF(m, top), QPointF(s - m, top))
    p.drawLine(QPointF(s * 0.4, m), QPointF(s * 0.6, m))
    path = QPainterPath()
    path.moveTo(l, top)
    path.lineTo(l + s * 0.06, s - m)
    path.lineTo(r - s * 0.06, s - m)
    path.lineTo(r, top)
    p.drawPath(path)


def _draw_select_all(p, s, m, c, pen):
    """Marching-ants selection rectangle."""
    dashed = QPen(pen)
    dashed.setStyle(Qt.DashLine)
    p.setPen(dashed)
    p.drawRect(QRectF(m, m, s - 2 * m, s - 2 * m))
    p.setPen(pen)


def _draw_hide(p, s, m, c, pen):
    """Eye with a slash."""
    _draw_show(p, s, m, c, pen)
    p.drawLine(QPointF(m, s - m), QPointF(s - m, m))


def _draw_show(p, s, m, c, pen):
    """Eye."""
    cy = s / 2
    path = QPainterPath()
    path.moveTo(m, cy)
    path.quadTo(s / 2, m, s - m, cy)
    path.quadTo(s / 2, s - m, m, cy)
    p.drawPath(path)
    p.drawEllipse(QPointF(s / 2, cy), s * 0.1, s * 0.1)


def _draw_print(p, s, m, c, pen):
    """Printer with paper."""
    p.drawRect(QRectF(m, s * 0.4, s - 2 * m, s * 0.32))
    p.drawRect(QRectF(m + s * 0.12, m, s - 2 * m - s * 0.24, s * 0.24))
    p.drawRect(QRectF(m + s * 0.12, s * 0.66, s - 2 * m - s * 0.24, s * 0.2))


def _draw_watch(p, s, m, c, pen):
    """Magnifier over a value list."""
    for y in (s * 0.26, s * 0.46):
        p.drawLine(QPointF(m, y), QPointF(s * 0.62, y))
    cx, cy = s * 0.58, s * 0.6
    r = s * 0.22
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx + r * 0.7, cy + r * 0.7), QPointF(s - m, s - m))


def _draw_xref(p, s, m, c, pen):
    """Cross reference — two boxes joined by an arrow."""
    p.drawRect(QRectF(m, m, s * 0.28, s * 0.28))
    p.drawRect(QRectF(s - m - s * 0.28, s - m - s * 0.28, s * 0.28, s * 0.28))
    p.drawLine(QPointF(m + s * 0.28, m + s * 0.28), QPointF(s - m - s * 0.28, s - m - s * 0.28))


def _draw_params(p, s, m, c, pen):
    """Slider list."""
    for i, y in enumerate((s * 0.28, s * 0.5, s * 0.72)):
        p.drawLine(QPointF(m, y), QPointF(s - m, y))
        cx = m + (s - 2 * m) * (0.3 + 0.25 * i)
        p.drawEllipse(QPointF(cx, y), s * 0.07, s * 0.07)


def _draw_named_sets(p, s, m, c, pen):
    """List with a chevron (enumerated picker)."""
    for y in (s * 0.3, s * 0.5, s * 0.7):
        p.drawLine(QPointF(m, y), QPointF(s * 0.62, y))
    p.drawLine(QPointF(s * 0.72, s * 0.42), QPointF(s * 0.82, s * 0.52))
    p.drawLine(QPointF(s * 0.82, s * 0.52), QPointF(s * 0.92, s * 0.42))


def _draw_module_props(p, s, m, c, pen):
    _draw_properties(p, s, m, c, pen)


def _draw_assign_io(p, s, m, c, pen):
    _draw_connect(p, s, m, c, pen)


def _draw_save_as(p, s, m, c, pen):
    _draw_save(p, s, m, c, pen)
    p.drawLine(QPointF(s - m * 0.6, s * 0.62), QPointF(s - m * 0.6, s * 0.92))
    p.drawLine(QPointF(s - m * 1.2, s * 0.77), QPointF(s, s * 0.77))


def _draw_exec_edit(p, s, m, c, pen):
    """Execution-order badge with a pencil."""
    f = QFont("Segoe UI", int(s * 0.42))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, -s * 0.08, s, s), Qt.AlignCenter, "#")
    p.drawLine(QPointF(m, s - m), QPointF(s - m, s * 0.55))


def _draw_faceplate(p, s, m, c, pen):
    cx, cy = s / 2, s * 0.55
    r = s * 0.32
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 0)
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 0, 180)
    p.drawPath(path)
    p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.5, cy - r * 0.65))


def _draw_debugger(p, s, m, c, pen):
    """Execution list with a breakpoint marker."""
    for y in (s * 0.27, s * 0.5, s * 0.73):
        p.drawLine(QPointF(s * 0.38, y), QPointF(s - m, y))
    p.setBrush(QColor("#D52235"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(s * 0.24, s * 0.5), s * 0.11, s * 0.11)


def _draw_pause(p, s, m, c, pen):
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    width = s * 0.18
    p.drawRect(QRectF(s * 0.27, m, width, s - 2 * m))
    p.drawRect(QRectF(s * 0.55, m, width, s - 2 * m))


def _draw_step_block(p, s, m, c, pen):
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(m, m),
                QPointF(s * 0.62, s / 2),
                QPointF(m, s - m),
            ]
        )
    )
    p.drawRect(QRectF(s * 0.7, m, s * 0.12, s - 2 * m))


def _draw_run_scan(p, s, m, c, pen):
    _draw_step_block(p, s, m, c, pen)
    p.setPen(QPen(c, max(1.0, s / 16)))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(m * 0.45, m * 0.45, s - m * 0.9, s - m * 0.9), 2, 2)


def _draw_ribbon_toggle(p, s, m, c, pen, *, collapse):
    """Bare chevron keeps the compact tab-row control visually quiet."""
    center_x, center_y = s * 0.5, s * 0.5
    half_width, height = s * 0.25, s * 0.18
    tip_y = center_y - height / 2 if collapse else center_y + height / 2
    edge_y = center_y + height / 2 if collapse else center_y - height / 2
    p.drawLine(QPointF(center_x - half_width, edge_y), QPointF(center_x, tip_y))
    p.drawLine(QPointF(center_x, tip_y), QPointF(center_x + half_width, edge_y))


def _draw_collapse_ribbon(p, s, m, c, pen):
    _draw_ribbon_toggle(p, s, m, c, pen, collapse=True)


def _draw_expand_ribbon(p, s, m, c, pen):
    _draw_ribbon_toggle(p, s, m, c, pen, collapse=False)


# Map icon names to draw functions
_DRAW_MAP = {
    "new": _draw_new,
    "open": _draw_open,
    "save": _draw_save,
    "presets": _draw_presets,
    "undo": _draw_undo,
    "redo": _draw_redo,
    "compile": _draw_compile,
    "download": _draw_download,
    "deactivate": _draw_deactivate,
    "zoom_in": _draw_zoom_in,
    "zoom_out": _draw_zoom_out,
    "zoom_fit": _draw_zoom_fit,
    "comment": _draw_comment,
    "values": _draw_values,
    "exec_order": _draw_exec_order,
    "procedure": _draw_procedure,
    "align": _draw_align,
    "auto": _draw_auto,
    "compare": _draw_compare,
    "history": _draw_history,
    "templates": _draw_templates,
    "search": _draw_search,
    "datalog": _draw_datalog,
    "connect": _draw_connect,
    "disconnect": _draw_disconnect,
    "io_config": _draw_io_config,
    "properties": _draw_properties,
    "status": _draw_status,
    "diagnostics": _draw_diagnostics,
    "upload": _draw_upload,
    "checkpoint": _draw_checkpoint,
    "restore": _draw_restore,
    "simulator": _draw_simulator,
    "faceplate": _draw_faceplate,
    "cut": _draw_cut,
    "copy": _draw_copy,
    "paste": _draw_paste,
    "delete": _draw_delete,
    "select_all": _draw_select_all,
    "hide": _draw_hide,
    "show": _draw_show,
    "print": _draw_print,
    "watch": _draw_watch,
    "xref": _draw_xref,
    "params": _draw_params,
    "named_sets": _draw_named_sets,
    "module_props": _draw_module_props,
    "assign_io": _draw_assign_io,
    "save_as": _draw_save_as,
    "exec_edit": _draw_exec_edit,
    "debugger": _draw_debugger,
    "pause": _draw_pause,
    "step_block": _draw_step_block,
    "run_scan": _draw_run_scan,
    "collapse_ribbon": _draw_collapse_ribbon,
    "expand_ribbon": _draw_expand_ribbon,
}
