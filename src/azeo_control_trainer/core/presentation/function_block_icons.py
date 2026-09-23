"""Procedural icon glyphs for strategy function blocks.

Each block type maps to a small drawing routine that paints a clean
line-art glyph into a QRect/QRectF.  Used in two places:

    * Block palette tree — a 16x16 QIcon next to the block name
    * Canvas block header — a 18x18 glyph beside the title

No image assets are shipped: every glyph is drawn from QPainter
primitives, so themes can recolour them and DPI scaling is automatic.

If a ``block_type`` has no specific drawer registered, the category
fallback is used (e.g. a math operator for MATH blocks).  This keeps
the icon coverage broad without needing 90 hand-tuned glyphs on day
one — specific glyphs can be added incrementally.

Public API:

    draw_block_icon(painter, rect, block_type, category=None, color=None)
        Paint the glyph for ``block_type`` into ``rect``.

    make_block_qicon(block_type, category=None, size=16, color=None) -> QIcon
        Convenience: return a cached QIcon at the given pixel size.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap,
    QPolygonF,
)

from azeo_control_trainer.core.strategy.model.block_base import BlockCategory
from .brand import AUTHORING_BLUE


# ────────────────────────────────────────────────────────────────────────
# Canvas glyphs keep their block-category meaning. Library QIcons use the
# shared authoring blue so the engineering chrome stays consistent.
# ────────────────────────────────────────────────────────────────────────
CATEGORY_COLOR: dict[BlockCategory, QColor] = {
    BlockCategory.IO:        QColor("#3A8F4F"),
    BlockCategory.CONTROL:   QColor("#1A4F8B"),
    BlockCategory.APC:       QColor("#7B2D8C"),
    BlockCategory.MATH:      QColor("#5A6B7D"),
    BlockCategory.SIGNAL:    QColor("#2E6E8A"),
    BlockCategory.LOGIC:     QColor("#8A6B1F"),
    BlockCategory.SAFETY:    QColor("#A4242F"),
    BlockCategory.SFC:       QColor("#9B5418"),
    BlockCategory.COMPOSITE: QColor("#5B3A7E"),
}


# Default mono font used inside glyph circles (Σ, π, etc.).
def _mono_font(size_pt: int, bold: bool = True) -> QFont:
    f = QFont("Consolas", size_pt)
    if bold:
        f.setBold(True)
    return f


# ────────────────────────────────────────────────────────────────────────
# Drawing primitives — common shapes used by multiple glyphs
# ────────────────────────────────────────────────────────────────────────

def _pen(color: QColor, width: float = 1.5) -> QPen:
    p = QPen(color, width)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _frame(r: QRectF, color: QColor, painter: QPainter, radius: float = 2.0):
    """Soft rounded outline around the glyph area — most glyphs use this."""
    painter.setPen(_pen(color, 1.2))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def _centered_text(painter: QPainter, r: QRectF, text: str, color: QColor,
                    font_size: int = 9):
    painter.setPen(_pen(color, 1.0))
    painter.setFont(_mono_font(font_size))
    painter.drawText(r, Qt.AlignCenter, text)


def _circle(painter: QPainter, cx: float, cy: float, r: float,
            color: QColor, fill: QColor | None = None):
    painter.setPen(_pen(color, 1.2))
    painter.setBrush(QBrush(fill) if fill else Qt.NoBrush)
    painter.drawEllipse(QPointF(cx, cy), r, r)


def _arrow(painter: QPainter, x1: float, y1: float, x2: float, y2: float,
            color: QColor, head_size: float = 3.0):
    painter.setPen(_pen(color, 1.4))
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    ang = math.atan2(y2 - y1, x2 - x1)
    p1 = QPointF(x2 - head_size * math.cos(ang - 0.5),
                  y2 - head_size * math.sin(ang - 0.5))
    p2 = QPointF(x2 - head_size * math.cos(ang + 0.5),
                  y2 - head_size * math.sin(ang + 0.5))
    poly = QPolygonF([QPointF(x2, y2), p1, p2])
    painter.setBrush(QBrush(color))
    painter.drawPolygon(poly)


# ────────────────────────────────────────────────────────────────────────
# Individual glyph drawers — keyed by block_type
# ────────────────────────────────────────────────────────────────────────

def _draw_pid(p: QPainter, r: QRectF, c: QColor):
    """PID — Σ in a circle with feedback loop."""
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.32
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "Σ", c, font_size=int(rad * 1.4))
    # feedback arrow
    p.setPen(_pen(c, 1.0))
    p.drawArc(QRectF(cx - rad * 1.5, cy - rad * 0.4, rad * 1.8, rad * 1.6),
              30 * 16, -200 * 16)


def _draw_ai(p: QPainter, r: QRectF, c: QColor):
    """AI — triangle pointing right (signal into block)."""
    cx, cy = r.center().x(), r.center().y()
    s = min(r.width(), r.height()) * 0.35
    poly = QPolygonF([QPointF(cx - s, cy - s), QPointF(cx + s, cy),
                       QPointF(cx - s, cy + s)])
    p.setPen(_pen(c, 1.4)); p.setBrush(QBrush(c.lighter(160)))
    p.drawPolygon(poly)


def _draw_ao(p: QPainter, r: QRectF, c: QColor):
    """AO — triangle pointing right with arrow exit."""
    cx, cy = r.center().x(), r.center().y()
    s = min(r.width(), r.height()) * 0.30
    poly = QPolygonF([QPointF(cx - s, cy - s), QPointF(cx, cy - s),
                       QPointF(cx + s, cy), QPointF(cx, cy + s),
                       QPointF(cx - s, cy + s)])
    p.setPen(_pen(c, 1.4)); p.setBrush(QBrush(c.lighter(160)))
    p.drawPolygon(poly)


def _draw_di(p: QPainter, r: QRectF, c: QColor):
    """DI — two stacked horizontal bars (discrete in)."""
    cx, cy = r.center().x(), r.center().y()
    s = min(r.width(), r.height()) * 0.35
    p.setPen(_pen(c, 1.6))
    p.drawLine(QPointF(cx - s, cy - s * 0.4), QPointF(cx + s, cy - s * 0.4))
    p.drawLine(QPointF(cx - s, cy + s * 0.4), QPointF(cx + s, cy + s * 0.4))


def _draw_do(p: QPainter, r: QRectF, c: QColor):
    """DO — square pulse glyph."""
    cx, cy = r.center().x(), r.center().y()
    s = min(r.width(), r.height()) * 0.35
    p.setPen(_pen(c, 1.5))
    p.drawPolyline(QPolygonF([
        QPointF(cx - s, cy + s * 0.6),
        QPointF(cx - s * 0.3, cy + s * 0.6),
        QPointF(cx - s * 0.3, cy - s * 0.6),
        QPointF(cx + s * 0.3, cy - s * 0.6),
        QPointF(cx + s * 0.3, cy + s * 0.6),
        QPointF(cx + s, cy + s * 0.6),
    ]))


def _draw_summer(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.34
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "+", c, font_size=int(rad * 1.6))


def _draw_sub(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.34
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "−", c, font_size=int(rad * 1.6))


def _draw_mult(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.34
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "×", c, font_size=int(rad * 1.5))


def _draw_div(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.34
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "÷", c, font_size=int(rad * 1.5))


def _draw_abs(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "|x|", c, font_size=int(r.height() * 0.45))


def _draw_sqrt(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "√", c, font_size=int(r.height() * 0.55))


def _draw_integrator(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "∫", c, font_size=int(r.height() * 0.65))


def _draw_derivative(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "d/dt", c, font_size=int(r.height() * 0.30))


def _draw_poly(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "f(x)", c, font_size=int(r.height() * 0.34))


def _draw_log(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "ln", c, font_size=int(r.height() * 0.42))


def _draw_pow(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "xⁿ", c, font_size=int(r.height() * 0.42))


def _draw_trig(p: QPainter, r: QRectF, c: QColor):
    """Single-cycle sine wave."""
    p.setPen(_pen(c, 1.4))
    n = 24
    path = QPainterPath()
    for i in range(n + 1):
        t = i / n
        x = r.left() + r.width() * (0.1 + 0.8 * t)
        y = r.center().y() - math.sin(2 * math.pi * t) * r.height() * 0.30
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    p.drawPath(path)


def _draw_sggn(p: QPainter, r: QRectF, c: QColor):
    """Sine + square overlap — signal generator."""
    p.setPen(_pen(c, 1.2))
    n = 18
    path = QPainterPath()
    for i in range(n + 1):
        t = i / n
        x = r.left() + r.width() * (0.1 + 0.8 * t)
        y = r.center().y() - math.sin(2 * math.pi * t) * r.height() * 0.25
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    p.drawPath(path)
    # square overlay
    p.setPen(_pen(c.darker(120), 1.0))
    h = r.height() * 0.18
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.15, r.center().y() + h),
        QPointF(r.left() + r.width() * 0.45, r.center().y() + h),
        QPointF(r.left() + r.width() * 0.45, r.center().y() - h),
        QPointF(r.left() + r.width() * 0.75, r.center().y() - h),
    ]))


def _draw_lookup(p: QPainter, r: QRectF, c: QColor):
    """Lookup / signal-char — XY curve with breakpoints."""
    p.setPen(_pen(c, 1.5))
    pts = [(0.1, 0.85), (0.35, 0.55), (0.6, 0.40), (0.9, 0.20)]
    path = QPainterPath()
    for i, (tx, ty) in enumerate(pts):
        x = r.left() + r.width() * tx
        y = r.top() + r.height() * ty
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    p.drawPath(path)
    for tx, ty in pts:
        x = r.left() + r.width() * tx
        y = r.top() + r.height() * ty
        p.setBrush(QBrush(c))
        p.drawEllipse(QPointF(x, y), 1.2, 1.2)


def _draw_filter(p: QPainter, r: QRectF, c: QColor):
    """First-order lag — exponential rise curve."""
    p.setPen(_pen(c, 1.5))
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.15)
    for i in range(1, 26):
        t = i / 25.0
        x = r.left() + r.width() * (0.1 + 0.8 * t)
        y = r.bottom() - r.height() * (0.15 + 0.7 * (1.0 - math.exp(-3.0 * t)))
        path.lineTo(x, y)
    p.drawPath(path)


def _draw_lead_lag(p: QPainter, r: QRectF, c: QColor):
    """Overshoot curve — lead/lag dynamic compensator."""
    p.setPen(_pen(c, 1.5))
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.15)
    for i in range(1, 26):
        t = i / 25.0
        x = r.left() + r.width() * (0.1 + 0.8 * t)
        e = 1.0 - math.exp(-4.0 * t)
        boost = 0.35 * math.exp(-6.0 * t) * t
        y = r.bottom() - r.height() * (0.15 + 0.7 * e + boost * 0.7)
        path.lineTo(x, y)
    p.drawPath(path)


def _draw_deadtime(p: QPainter, r: QRectF, c: QColor):
    """Deadtime — flat then step."""
    p.setPen(_pen(c, 1.5))
    y_lo = r.bottom() - r.height() * 0.20
    y_hi = r.top() + r.height() * 0.20
    x0 = r.left() + r.width() * 0.10
    x1 = r.left() + r.width() * 0.50
    x2 = r.right() - r.width() * 0.10
    p.drawPolyline(QPolygonF([
        QPointF(x0, y_lo), QPointF(x1, y_lo),
        QPointF(x1, y_hi), QPointF(x2, y_hi)]))


def _draw_ramp(p: QPainter, r: QRectF, c: QColor):
    """Ramp — straight slope."""
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.15),
               QPointF(r.right() - r.width() * 0.1, r.top() + r.height() * 0.15))


def _draw_ramp_soak(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    y_lo = r.bottom() - r.height() * 0.20
    y_hi = r.top() + r.height() * 0.25
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.1, y_lo),
        QPointF(r.left() + r.width() * 0.35, y_hi),
        QPointF(r.right() - r.width() * 0.35, y_hi),
        QPointF(r.right() - r.width() * 0.10, y_lo)]))


def _draw_scaler(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "%", c, font_size=int(r.height() * 0.55))


def _draw_limit(p: QPainter, r: QRectF, c: QColor):
    """Limiter — sloped line with horizontal clips top + bottom."""
    p.setPen(_pen(c, 1.4))
    y_top = r.top() + r.height() * 0.25
    y_bot = r.bottom() - r.height() * 0.25
    x0 = r.left() + r.width() * 0.10
    x1 = r.right() - r.width() * 0.10
    p.drawPolyline(QPolygonF([
        QPointF(x0, y_bot),
        QPointF(r.left() + r.width() * 0.35, y_bot),
        QPointF(r.right() - r.width() * 0.35, y_top),
        QPointF(x1, y_top)]))


def _draw_deadband(p: QPainter, r: QRectF, c: QColor):
    """Deadband — symmetric clip near origin."""
    p.setPen(_pen(c, 1.4))
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.4
    p.drawPolyline(QPolygonF([
        QPointF(cx - s, cy + s * 0.6),
        QPointF(cx - s * 0.25, cy),
        QPointF(cx + s * 0.25, cy),
        QPointF(cx + s, cy - s * 0.6)]))


def _draw_rate_limit(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.4
    # target step
    p.drawPolyline(QPolygonF([
        QPointF(cx - s, cy + s * 0.5),
        QPointF(cx - s * 0.1, cy + s * 0.5),
        QPointF(cx - s * 0.1, cy - s * 0.5),
        QPointF(cx + s, cy - s * 0.5)]))
    # filtered version with finite slope
    p.setPen(_pen(c.lighter(140), 1.2))
    p.drawPolyline(QPolygonF([
        QPointF(cx - s, cy + s * 0.5),
        QPointF(cx + s * 0.5, cy - s * 0.5),
        QPointF(cx + s, cy - s * 0.5)]))


def _draw_select_hi(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, ">", c, font_size=int(r.height() * 0.60))


def _draw_select_lo(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "<", c, font_size=int(r.height() * 0.60))


def _draw_select_mid(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "=", c, font_size=int(r.height() * 0.55))


def _draw_switch(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.5))
    x0 = r.left() + r.width() * 0.10
    x1 = r.right() - r.width() * 0.10
    cy = r.center().y()
    p.drawLine(QPointF(x0, cy), QPointF(x0 + r.width() * 0.3, cy))
    p.drawLine(QPointF(x0 + r.width() * 0.3, cy),
               QPointF(x1 - r.width() * 0.3, cy - r.height() * 0.25))
    p.drawLine(QPointF(x1 - r.width() * 0.3, cy), QPointF(x1, cy))
    _circle(p, x0 + r.width() * 0.3, cy, 1.5, c, c)


def _draw_mux(p: QPainter, r: QRectF, c: QColor):
    """Mux — trapezoid."""
    p.setPen(_pen(c, 1.4))
    pts = [QPointF(r.left() + r.width() * 0.15, r.top() + r.height() * 0.15),
            QPointF(r.right() - r.width() * 0.15, r.top() + r.height() * 0.30),
            QPointF(r.right() - r.width() * 0.15, r.bottom() - r.height() * 0.30),
            QPointF(r.left() + r.width() * 0.15, r.bottom() - r.height() * 0.15)]
    p.drawPolygon(QPolygonF(pts))


def _draw_demux(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    pts = [QPointF(r.left() + r.width() * 0.15, r.top() + r.height() * 0.30),
            QPointF(r.right() - r.width() * 0.15, r.top() + r.height() * 0.15),
            QPointF(r.right() - r.width() * 0.15, r.bottom() - r.height() * 0.15),
            QPointF(r.left() + r.width() * 0.15, r.bottom() - r.height() * 0.30)]
    p.drawPolygon(QPolygonF(pts))


def _draw_and(p: QPainter, r: QRectF, c: QColor):
    """AND — D-shape."""
    path = QPainterPath()
    left = r.left() + r.width() * 0.15
    right = r.right() - r.width() * 0.15
    top = r.top() + r.height() * 0.25
    bot = r.bottom() - r.height() * 0.25
    mid_y = r.center().y()
    path.moveTo(left, top)
    path.lineTo((left + right) / 2, top)
    path.arcTo(QRectF(left, top, right - left, bot - top), 90, -180)
    path.lineTo(left, bot)
    path.closeSubpath()
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _draw_or(p: QPainter, r: QRectF, c: QColor):
    """OR — shield shape."""
    path = QPainterPath()
    left = r.left() + r.width() * 0.18
    right = r.right() - r.width() * 0.10
    top = r.top() + r.height() * 0.25
    bot = r.bottom() - r.height() * 0.25
    path.moveTo(left, top)
    path.quadTo(QPointF((left + right) / 2, top + 1), QPointF(right, r.center().y()))
    path.quadTo(QPointF((left + right) / 2, bot - 1), QPointF(left, bot))
    path.quadTo(QPointF(left + 4, r.center().y()), QPointF(left, top))
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _draw_not(p: QPainter, r: QRectF, c: QColor):
    """NOT — triangle with bubble."""
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.32
    poly = QPolygonF([QPointF(cx - s, cy - s), QPointF(cx + s * 0.6, cy),
                       QPointF(cx - s, cy + s)])
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawPolygon(poly)
    p.drawEllipse(QPointF(cx + s * 0.6 + 2, cy), 1.6, 1.6)


def _draw_xor(p: QPainter, r: QRectF, c: QColor):
    _draw_or(p, r, c)
    # extra curve at the input side
    p.setPen(_pen(c, 1.2))
    path = QPainterPath()
    left = r.left() + r.width() * 0.12
    top = r.top() + r.height() * 0.25
    bot = r.bottom() - r.height() * 0.25
    path.moveTo(left, top)
    path.quadTo(QPointF(left + 4, r.center().y()), QPointF(left, bot))
    p.drawPath(path)


def _draw_nand(p: QPainter, r: QRectF, c: QColor):
    _draw_and(p, r, c)
    cx = r.right() - r.width() * 0.10
    p.drawEllipse(QPointF(cx, r.center().y()), 1.6, 1.6)


def _draw_nor(p: QPainter, r: QRectF, c: QColor):
    _draw_or(p, r, c)
    cx = r.right() - r.width() * 0.06
    p.drawEllipse(QPointF(cx, r.center().y()), 1.6, 1.6)


def _draw_pos_edge(p: QPainter, r: QRectF, c: QColor):
    """Rising edge marker."""
    p.setPen(_pen(c, 1.5))
    y_lo = r.bottom() - r.height() * 0.25
    y_hi = r.top() + r.height() * 0.25
    cx = r.center().x()
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.1, y_lo),
        QPointF(cx, y_lo),
        QPointF(cx, y_hi),
        QPointF(r.right() - r.width() * 0.1, y_hi)]))


def _draw_neg_edge(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.5))
    y_lo = r.bottom() - r.height() * 0.25
    y_hi = r.top() + r.height() * 0.25
    cx = r.center().x()
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.1, y_hi),
        QPointF(cx, y_hi),
        QPointF(cx, y_lo),
        QPointF(r.right() - r.width() * 0.1, y_lo)]))


def _draw_latch(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.18, r.height() * 0.20,
                           -r.width() * 0.18, -r.height() * 0.20))
    _centered_text(p, r, "SR", c, font_size=int(r.height() * 0.30))


def _draw_rs(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.18, r.height() * 0.20,
                           -r.width() * 0.18, -r.height() * 0.20))
    _centered_text(p, r, "RS", c, font_size=int(r.height() * 0.30))


def _draw_clock(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rad = min(r.width(), r.height()) * 0.34
    _circle(p, cx, cy, rad, c)
    p.setPen(_pen(c, 1.4))
    p.drawLine(QPointF(cx, cy), QPointF(cx, cy - rad * 0.7))
    p.drawLine(QPointF(cx, cy), QPointF(cx + rad * 0.5, cy))


def _draw_pulse(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.5))
    y_lo = r.bottom() - r.height() * 0.25
    y_hi = r.top() + r.height() * 0.25
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.1, y_lo),
        QPointF(r.center().x() - r.width() * 0.18, y_lo),
        QPointF(r.center().x() - r.width() * 0.18, y_hi),
        QPointF(r.center().x() + r.width() * 0.18, y_hi),
        QPointF(r.center().x() + r.width() * 0.18, y_lo),
        QPointF(r.right() - r.width() * 0.1, y_lo)]))


def _draw_counter(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "123", c, font_size=int(r.height() * 0.30))


def _draw_comparator(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "<=>", c, font_size=int(r.height() * 0.30))


def _draw_ratio(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "n:1", c, font_size=int(r.height() * 0.36))


def _draw_splitter(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.32
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(cx - s, cy), QPointF(cx, cy))
    p.drawLine(QPointF(cx, cy), QPointF(cx + s, cy - s * 0.6))
    p.drawLine(QPointF(cx, cy), QPointF(cx + s, cy + s * 0.6))


def _draw_voter(p: QPainter, r: QRectF, c: QColor):
    cx, cy = r.center().x(), r.center().y()
    rr = r.width() * 0.10
    _circle(p, cx - r.width() * 0.20, cy - r.height() * 0.10, rr, c)
    _circle(p, cx + r.width() * 0.20, cy - r.height() * 0.10, rr, c)
    _circle(p, cx, cy + r.height() * 0.20, rr, c, fill=c.lighter(140))


def _draw_interlock(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.20, r.height() * 0.30,
                           -r.width() * 0.20, -r.height() * 0.10))
    # padlock loop
    p.drawArc(QRectF(r.center().x() - r.width() * 0.12,
                      r.top() + r.height() * 0.10,
                      r.width() * 0.24, r.height() * 0.40),
              0, 180 * 16)


def _draw_alarm(p: QPainter, r: QRectF, c: QColor):
    """Alarm detection is a bell; a padlock means an interlock."""
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    w, h = r.width(), r.height()
    path = QPainterPath(QPointF(cx - w * .3, cy + h * .2))
    path.lineTo(cx - w * .22, cy + h * .08)
    path.lineTo(cx - w * .22, cy - h * .12)
    path.cubicTo(cx - w * .22, cy - h * .42,
                 cx + w * .22, cy - h * .42, cx + w * .22, cy - h * .12)
    path.lineTo(cx + w * .22, cy + h * .08)
    path.lineTo(cx + w * .3, cy + h * .2)
    path.closeSubpath()
    p.drawPath(path)
    p.drawArc(QRectF(cx - w * .08, cy + h * .22, w * .16, h * .16),
              180 * 16, 180 * 16)


def _draw_sequence(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.3))
    p.setBrush(Qt.NoBrush)
    for index in range(3):
        y = r.top() + r.height() * (.08 + index * .31)
        p.drawRect(QRectF(r.left() + r.width() * .25, y,
                         r.width() * .5, r.height() * .21))
        if index < 2:
            p.drawLine(QPointF(r.center().x(), y + r.height() * .21),
                       QPointF(r.center().x(), y + r.height() * .31))


def _draw_states(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.3))
    p.setBrush(Qt.NoBrush)
    for x, y in ((.25, .28), (.72, .72)):
        _circle(p, r.left() + r.width() * x, r.top() + r.height() * y,
                r.width() * .17, c)
    _arrow(p, r.left() + r.width() * .44, r.top() + r.height() * .32,
           r.left() + r.width() * .66, r.top() + r.height() * .5, c,
           head_size=r.width() * .1)


def _draw_cem(p: QPainter, r: QRectF, c: QColor):
    """CEM — 3x3 dot matrix."""
    p.setPen(_pen(c, 1.0))
    p.setBrush(QBrush(c))
    for i in range(3):
        for j in range(3):
            x = r.left() + r.width() * (0.25 + 0.25 * j)
            y = r.top() + r.height() * (0.25 + 0.25 * i)
            if (i + j) % 2 == 0:
                p.drawEllipse(QPointF(x, y), 1.3, 1.3)
            else:
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x, y), 1.3, 1.3)
                p.setBrush(QBrush(c))


def _draw_manld(p: QPainter, r: QRectF, c: QColor):
    """Manual loader — operator hand on a knob."""
    cx, cy = r.center().x(), r.center().y()
    rad = r.width() * 0.30
    _circle(p, cx, cy + r.height() * 0.05, rad, c)
    p.setPen(_pen(c, 1.6))
    # pointer
    p.drawLine(QPointF(cx, cy + r.height() * 0.05),
               QPointF(cx + rad * 0.7, cy - rad * 0.4))
    # M label
    p.setFont(_mono_font(int(r.height() * 0.22)))
    p.drawText(r.adjusted(0, 0, -r.width() * 0.55, r.height() * 0.45),
               Qt.AlignCenter, "M")


def _draw_at(p: QPainter, r: QRectF, c: QColor):
    """Analog tracking — arrow following a curve."""
    p.setPen(_pen(c, 1.3))
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.2)
    path.quadTo(QPointF(r.center().x(), r.top() + r.height() * 0.1),
                QPointF(r.right() - r.width() * 0.25, r.center().y()))
    p.drawPath(path)
    _arrow(p, r.right() - r.width() * 0.30, r.center().y(),
           r.right() - r.width() * 0.10, r.top() + r.height() * 0.35, c, head_size=2.5)


def _draw_cnd(p: QPainter, r: QRectF, c: QColor):
    """Condition — diamond decision shape with question mark."""
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.32
    poly = QPolygonF([QPointF(cx, cy - s), QPointF(cx + s, cy),
                       QPointF(cx, cy + s), QPointF(cx - s, cy)])
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawPolygon(poly)
    _centered_text(p, r, "?", c, font_size=int(s * 1.3))


def _draw_act(p: QPainter, r: QRectF, c: QColor):
    """ACT — code brackets."""
    p.setPen(_pen(c, 1.4))
    _centered_text(p, r, "{ }", c, font_size=int(r.height() * 0.40))


def _draw_expression(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "=", c, font_size=int(r.height() * 0.55))


def _draw_dev(p: QPainter, r: QRectF, c: QColor):
    """Motor/device — circle with M."""
    cx, cy = r.center().x(), r.center().y()
    rad = r.width() * 0.30
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "M", c, font_size=int(rad * 1.2))


def _draw_valve(p: QPainter, r: QRectF, c: QColor):
    """Valve — bow-tie."""
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.30
    poly = QPolygonF([QPointF(cx - s, cy - s), QPointF(cx + s, cy + s),
                       QPointF(cx + s, cy - s), QPointF(cx - s, cy + s)])
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawPolygon(poly)


def _draw_step(p: QPainter, r: QRectF, c: QColor):
    """SFC step — rectangle."""
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.15, r.height() * 0.30,
                           -r.width() * 0.15, -r.height() * 0.30))


def _draw_initial_step(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.20, r.height() * 0.30,
                           -r.width() * 0.20, -r.height() * 0.30))
    p.drawRect(r.adjusted(r.width() * 0.15, r.height() * 0.25,
                           -r.width() * 0.15, -r.height() * 0.25))


def _draw_end_step(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4)); p.setBrush(QBrush(c.lighter(180)))
    p.drawRect(r.adjusted(r.width() * 0.15, r.height() * 0.30,
                           -r.width() * 0.15, -r.height() * 0.30))


def _draw_transition(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.6))
    p.drawLine(QPointF(r.left() + r.width() * 0.15, r.center().y()),
               QPointF(r.right() - r.width() * 0.15, r.center().y()))


def _draw_parallel_split(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.8))
    cy = r.center().y()
    p.drawLine(QPointF(r.left() + r.width() * 0.10, cy - 1.5),
               QPointF(r.right() - r.width() * 0.10, cy - 1.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.10, cy + 1.5),
               QPointF(r.right() - r.width() * 0.10, cy + 1.5))


def _draw_composite(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    inner = r.adjusted(r.width() * 0.20, r.height() * 0.25,
                        -r.width() * 0.20, -r.height() * 0.25)
    p.drawRect(inner)
    inner2 = inner.adjusted(2, 2, -2, -2)
    p.drawRect(inner2)


def _draw_inport(p: QPainter, r: QRectF, c: QColor):
    _arrow(p, r.left() + r.width() * 0.15, r.center().y(),
           r.right() - r.width() * 0.20, r.center().y(), c, head_size=3.5)


def _draw_outport(p: QPainter, r: QRectF, c: QColor):
    _arrow(p, r.left() + r.width() * 0.20, r.center().y(),
           r.right() - r.width() * 0.10, r.center().y(), c, head_size=3.5)


def _draw_parameter_item(p: QPainter, r: QRectF, c: QColor, label: str,
                         *, source: bool, internal: bool):
    """Draw a typed module parameter with its signal direction visible."""
    cy = r.center().y()
    pad = r.width() * 0.10
    body_width = r.width() * 0.48
    if source:
        body = QRectF(r.left() + pad, r.top() + r.height() * 0.22,
                      body_width, r.height() * 0.56)
        _arrow(p, body.right(), cy, r.right() - pad, cy, c,
               head_size=max(2.0, r.width() * 0.14))
    else:
        body = QRectF(r.right() - pad - body_width,
                      r.top() + r.height() * 0.22,
                      body_width, r.height() * 0.56)
        _arrow(p, r.left() + pad, cy, body.left(), cy, c,
               head_size=max(2.0, r.width() * 0.14))
    p.setPen(_pen(c, 1.2))
    p.setBrush(Qt.NoBrush)
    if internal:
        p.drawEllipse(body)
    else:
        p.drawRoundedRect(body, 1.5, 1.5)
    _centered_text(p, body, label, c,
                   font_size=max(4, int(r.height() * 0.30)))


def _draw_input_parameter(p: QPainter, r: QRectF, c: QColor):
    _draw_parameter_item(p, r, c, "I", source=True, internal=False)


def _draw_output_parameter(p: QPainter, r: QRectF, c: QColor):
    _draw_parameter_item(p, r, c, "O", source=False, internal=False)


def _draw_internal_read_parameter(p: QPainter, r: QRectF, c: QColor):
    _draw_parameter_item(p, r, c, "R", source=True, internal=True)


def _draw_internal_write_parameter(p: QPainter, r: QRectF, c: QColor):
    _draw_parameter_item(p, r, c, "W", source=False, internal=True)


def _draw_constant(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "K", c, font_size=int(r.height() * 0.55))


def _draw_setpoint(p: QPainter, r: QRectF, c: QColor):
    _centered_text(p, r, "SP", c, font_size=int(r.height() * 0.40))


def _draw_memory(p: QPainter, r: QRectF, c: QColor):
    p.setPen(_pen(c, 1.4)); p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(r.width() * 0.15, r.height() * 0.30,
                           -r.width() * 0.15, -r.height() * 0.30))
    p.drawLine(QPointF(r.left() + r.width() * 0.30, r.top() + r.height() * 0.30),
               QPointF(r.left() + r.width() * 0.30, r.bottom() - r.height() * 0.30))


def _draw_data(p: QPainter, r: QRectF, c: QColor):
    """Stack icon — for FIFO / LIFO / DATALOG."""
    p.setPen(_pen(c, 1.3))
    for i in range(3):
        y = r.top() + r.height() * (0.30 + 0.18 * i)
        p.drawLine(QPointF(r.left() + r.width() * 0.20, y),
                   QPointF(r.right() - r.width() * 0.20, y))


def _draw_apc(p: QPainter, r: QRectF, c: QColor):
    """APC / DMC — gear-like."""
    cx, cy = r.center().x(), r.center().y()
    rad = r.width() * 0.30
    _circle(p, cx, cy, rad, c)
    _centered_text(p, r, "MPC", c, font_size=int(rad * 0.7))


def _draw_bms(p: QPainter, r: QRectF, c: QColor):
    """Burner / flame icon."""
    path = QPainterPath()
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.32
    path.moveTo(cx, cy + s)
    path.cubicTo(QPointF(cx - s, cy), QPointF(cx - s * 0.4, cy - s),
                  QPointF(cx, cy - s * 1.2))
    path.cubicTo(QPointF(cx + s * 0.4, cy - s), QPointF(cx + s, cy),
                  QPointF(cx, cy + s))
    p.setPen(_pen(c, 1.4)); p.setBrush(QBrush(c.lighter(160)))
    p.drawPath(path)


# ────────────────────────────────────────────────────────────────────────
# Block-type → drawer map
# ────────────────────────────────────────────────────────────────────────
_DRAWERS: dict[str, Callable] = {
    # IO
    "AI": _draw_ai, "AO": _draw_ao, "DI": _draw_di, "DO": _draw_do,
    "ALARM": _draw_alarm, "ALARM_DET": _draw_alarm,
    # Control
    "PID": _draw_pid,
    "SCALER": _draw_scaler, "RATIO": _draw_ratio,
    "LEAD_LAG": _draw_lead_lag, "DEADTIME": _draw_deadtime,
    "FILTER": _draw_filter, "MOVING_AVG": _draw_filter,
    "RAMP": _draw_ramp, "RAMP_SOAK": _draw_ramp_soak,
    "BIAS": _draw_summer, "TRANSFER": _draw_switch,
    "SIGNAL_CHAR": _draw_lookup,
    "MANLD": _draw_manld, "AT": _draw_at,
    "SPLITTER": _draw_splitter, "ONOFF": _draw_switch,
    "GAIN_SCHED": _draw_lookup,
    # APC
    "DMC_CONTROLLER": _draw_apc, "APC_CONTROL": _draw_apc,
    "EXT_DMC_BRIDGE": _draw_apc, "WATCHDOG": _draw_clock,
    "SHED_LOGIC": _draw_switch, "SP_HANDOFF": _draw_switch,
    "MV_CLAMP": _draw_limit, "HEAT_BAL_PREDICTOR": _draw_apc,
    # Math
    "SUMMER": _draw_summer, "SUB": _draw_sub,
    "MULTIPLIER": _draw_mult, "DIVIDER": _draw_div,
    "ABS": _draw_abs, "SQRT": _draw_sqrt,
    "INTEGRATOR": _draw_integrator, "DERIVATIVE": _draw_derivative,
    "TOTALIZER": _draw_integrator,
    "POLY": _draw_poly, "LOG_EXP": _draw_log, "POWER": _draw_pow,
    "TRIG": _draw_trig, "FLOW_COMP": _draw_sqrt,
    "STATISTICS": _draw_summer, "LOOKUP": _draw_lookup,
    "BTU_CALC": _draw_summer,
    # Signal
    "SGGN": _draw_sggn,
    # Logic
    "AND": _draw_and, "OR": _draw_or, "NOT": _draw_not,
    "NAND": _draw_nand, "NOR": _draw_nor, "XOR": _draw_xor,
    "TIMER_ON": _draw_clock, "TIMER_OFF": _draw_clock,
    "COMPARATOR": _draw_comparator,
    "SR_LATCH": _draw_latch, "RS_LATCH": _draw_rs,
    "POS_EDGE": _draw_pos_edge, "NEG_EDGE": _draw_neg_edge,
    "PULSE": _draw_pulse, "COUNTER": _draw_counter,
    "TRUTH_TABLE": _draw_cem, "SCHEDULE": _draw_clock,
    "SEQ_TIMER": _draw_clock, "CND": _draw_cnd,
    # Selectors
    "MIN_SELECT": _draw_select_lo, "MAX_SELECT": _draw_select_hi,
    "MID_SELECT": _draw_select_mid, "AVG_SELECT": _draw_select_mid,
    "SWITCH": _draw_switch, "MODE_SWITCH": _draw_switch,
    "MUX": _draw_mux, "DEMUX": _draw_demux,
    # Limiters
    "LIMITER": _draw_limit, "DEADBAND": _draw_deadband,
    "RATE_LIMITER": _draw_rate_limit,
    # Safety
    "SIS_VOTER": _draw_voter, "FOL_LOGIC": _draw_interlock,
    "INTERLOCK": _draw_interlock, "CEM": _draw_cem,
    # SFC
    "SEQ": _draw_sequence, "SFC_CHART": _draw_sequence, "STD": _draw_states,
    "STEP": _draw_step, "INITIAL_STEP": _draw_initial_step,
    "END_STEP": _draw_end_step, "TRANSITION": _draw_transition,
    "SFC_ACTION": _draw_act, "PARALLEL_SPLIT": _draw_parallel_split,
    "PARALLEL_JOIN": _draw_parallel_split,
    "SELECTOR_BRANCH": _draw_switch,
    # Composite
    "COMPOSITE": _draw_composite,
    "INPORT": _draw_inport, "OUTPORT": _draw_outport,
    # Azeo module parameter special items
    "INPUT_PARAMETER": _draw_input_parameter,
    "OUTPUT_PARAMETER": _draw_output_parameter,
    "INTERNAL_READ_PARAMETER": _draw_internal_read_parameter,
    "INTERNAL_WRITE_PARAMETER": _draw_internal_write_parameter,
    # Utility
    "CONSTANT": _draw_constant, "SETPOINT": _draw_setpoint,
    "MEM_FLOAT": _draw_memory, "MEM_BOOL": _draw_memory,
    "MEM_INT": _draw_memory, "MEM_STRING": _draw_memory,
    # Devices
    "DEVCTL": _draw_dev, "VLVCTL": _draw_valve,
    # Scripted
    "ACT": _draw_act, "EXPRESSION": _draw_expression,
    # Data
    "FIFO": _draw_data, "LIFO": _draw_data, "DATALOG": _draw_data,
    "BIT_PACK": _draw_data, "BIT_UNPACK": _draw_data, "MSG": _draw_data,
    # BMS
    "BURNER_SEQ": _draw_bms, "FLAME_DET": _draw_bms,
    "FUEL_VALVE": _draw_valve, "BLOWER": _draw_dev,
    "PURGE_TIMER": _draw_clock, "TRIP_RELAY": _draw_interlock,
}


# Category-level fallback drawer (used when no specific drawer registered)
def _draw_category_default(p: QPainter, r: QRectF, c: QColor,
                            category: BlockCategory | None):
    if category == BlockCategory.MATH:
        _centered_text(p, r, "ƒ", c, font_size=int(r.height() * 0.55))
    elif category == BlockCategory.LOGIC:
        _centered_text(p, r, "&", c, font_size=int(r.height() * 0.45))
    elif category == BlockCategory.IO:
        _draw_ai(p, r, c)
    elif category == BlockCategory.SAFETY:
        _draw_interlock(p, r, c)
    elif category == BlockCategory.SFC:
        _draw_step(p, r, c)
    elif category == BlockCategory.COMPOSITE:
        _draw_composite(p, r, c)
    elif category == BlockCategory.APC:
        _draw_apc(p, r, c)
    elif category == BlockCategory.SIGNAL:
        _draw_filter(p, r, c)
    elif category == BlockCategory.CONTROL:
        _draw_pid(p, r, c)
    else:
        # Truly unknown — small dot
        cx, cy = r.center().x(), r.center().y()
        _circle(p, cx, cy, r.width() * 0.15, c, c)


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

def draw_block_icon(painter: QPainter, rect: QRectF,
                     block_type: str,
                     category: BlockCategory | None = None,
                     color: QColor | None = None,
                     show_frame: bool = False) -> None:
    """Paint the glyph for ``block_type`` into ``rect``.

    ``color`` overrides the default category stroke colour. When
    ``show_frame`` is True, a soft outline is drawn around the glyph —
    useful in palette icons for crisper edges; canvas headers usually
    omit it.
    """
    if color is None:
        color = CATEGORY_COLOR.get(category, QColor("#444444"))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    if show_frame:
        _frame(rect, color.darker(140), painter, radius=3)
    drawer = _DRAWERS.get(block_type)
    if drawer is not None:
        drawer(painter, rect, color)
    else:
        _draw_category_default(painter, rect, color, category)
    painter.restore()


@lru_cache(maxsize=512)
def _icon_pixmap(block_type: str, category_value: int | None, size: int,
                  color_rgb: int, dpr: float) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    category = BlockCategory(category_value) if category_value is not None else None
    color = QColor.fromRgb(color_rgb) if color_rgb else None
    draw_block_icon(painter, QRectF(0, 0, size, size),
                     block_type, category, color, show_frame=True)
    painter.end()
    return pm


def make_block_qicon(block_type: str,
                      category: BlockCategory | None = None,
                      size: int = 16,
                      color: QColor | None = None) -> QIcon:
    """Engineering glyph at screen DPI; canvas category colors stay in the painter."""
    cat_v = category.value if category is not None else None
    col_v = (color if color is not None else QColor(AUTHORING_BLUE)).rgb()
    app = QGuiApplication.instance()
    dpr = max(1.0, app.devicePixelRatio()) if app else 1.0
    return QIcon(_icon_pixmap(block_type, cat_v, size, col_v, dpr))
