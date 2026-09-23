"""A measured semicircular PVM; all process meaning comes from its bindings."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from ..theme.roles import Role
from .hp.state import PRIORITY_ROLES
from .vessel_trend import finite_good, limit_fractions


def measurement(result):
    value = finite_good(result)
    limits = result.eu_range if result else None
    if value is None or not isinstance(limits, (tuple, list)) or len(limits) != 2:
        return None
    lo, hi = limits
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) for v in (lo, hi)) or hi <= lo:
        return None
    return value, lo, hi, max(0.0, min(1.0, (value - lo) / (hi - lo)))


def paint(painter, item):
    rect, palette = item.rect(), item._palette
    result = item.binding.result if item.binding else None
    reading = measurement(result)
    radius = max(1.0, min((rect.width() - 20) / 2, rect.height() - 70))
    center = QPointF(rect.center().x(), rect.top() + radius + 9)
    circle = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
    painter.setRenderHint(QPainter.Antialiasing, True)
    # These neutral sectors divide the scale, not plant operating zones.
    # Actual limits are separate tick marks and never invented default bands.
    tones = (Role.EQUIPMENT, Role.BAR_PV, Role.EQUIPMENT_FILL,
             Role.SURFACE_PANEL, Role.EQUIPMENT_FILL, Role.BAR_PV)
    painter.setPen(QPen(QColor(palette[Role.SURFACE_BG]), .7))
    for index, role in enumerate(tones):
        painter.setBrush(QColor(palette[role]))
        painter.drawPie(circle, (180 - (index + 1) * 30) * 16, 30 * 16)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(palette[Role.EQUIPMENT]), .8))
    painter.drawArc(circle, 0, 180 * 16)
    painter.drawLine(QPointF(circle.left(), center.y()), QPointF(circle.right(), center.y()))

    def point(fraction, length):
        angle = math.pi * fraction
        return QPointF(center.x() - length * math.cos(angle),
                       center.y() - length * math.sin(angle))

    if reading is not None:
        value, lo, hi, fraction = reading
        painter.setPen(QPen(QColor(palette[Role.TEXT_DIM]), 1.2))
        for _, at in limit_fractions(item, (lo, hi)):
            painter.drawLine(point(at, radius * .85), point(at, radius))
        role = (PRIORITY_ROLES.get(result.alarm_priority, Role.ALARM_P3)
                if result.alarm_active else Role.TEXT)
        painter.setPen(QPen(QColor(palette[role]), 2))
        painter.drawLine(center, point(fraction, radius * .92))
        painter.setBrush(QColor(palette[role]))
        painter.drawEllipse(center, 2.5, 2.5)
        value_text = f"{value:.2f}" if abs(value) < 100000 else f"{value:.3g}"
        painter.setFont(item.typeface("secondary", "Segoe UI", 8))
        painter.setPen(QColor(palette[Role.TEXT_DIM]))
        painter.drawText(QRectF(circle.left(), center.y() + 3, radius, 16), Qt.AlignLeft, f"{lo:g}")
        painter.drawText(QRectF(center.x(), center.y() + 3, radius, 16), Qt.AlignRight, f"{hi:g}")
    else:
        value_text = "No range" if finite_good(result) is not None else "No good data"
    painter.setFont(item.typeface("value", "Segoe UI", 11, QFont.Bold))
    painter.setPen(QColor(palette[Role.TEXT]))
    painter.drawText(QRectF(rect.left(), center.y() + 21, rect.width(), 22), Qt.AlignCenter, value_text)
    painter.setFont(item.typeface("secondary", "Segoe UI", 8))
    painter.setPen(QColor(palette[Role.TEXT_DIM]))
    units = str(result.units or "") if result else ""
    if result and result.forced:
        units = "FORCED  " + units
    painter.drawText(QRectF(rect.left(), center.y() + 43, rect.width(), 17), Qt.AlignCenter, units)
    if result and result.alarm_active:
        from .pvm_painters import draw_alarm_badge
        draw_alarm_badge(painter, rect.right() - 18, rect.top(),
                         result.alarm_priority, str(result.alarm_priority), palette=palette,
                         font=item.typeface("alarm", "Segoe UI", 8, QFont.Bold))
