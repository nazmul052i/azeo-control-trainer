"""The existing vessel PVM's measured level, limits and recent-history anatomy."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPen

from ..theme.roles import Role
from .hp.state import PRIORITY_ROLES


def finite_good(result):
    if result is None or result.quality.name != "GOOD":
        return None
    value = result.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def limit_fractions(item, eu):
    """Absent or bad limits leave no guide; invented thirds look engineered."""
    if not eu or not all(math.isfinite(v) for v in eu) or eu[1] <= eu[0]:
        return ()
    limits = []
    for name in ("LO", "HI"):
        binding = item.rows.get(name)
        value = finite_good(binding.result if binding else None)
        if value is not None and eu[0] <= value <= eu[1]:
            limits.append((name, (value - eu[0]) / (eu[1] - eu[0])))
    return tuple(limits)


def paint(painter, item):
    from .pvm_painters import _fraction, _svg_shell, draw_alarm_badge

    rect = item.rect()
    result = item.binding.result if item.binding else None
    value = finite_good(result)
    eu = result.eu_range if result else None
    if not eu or not all(math.isfinite(v) for v in eu) or eu[1] <= eu[0]:
        eu = None
    palette = item._palette
    line = QColor(palette[Role.LINE])
    trace = QColor(palette[Role.ACTION])
    alarm = bool(result and result.alarm_active)
    level = QColor(palette[PRIORITY_ROLES.get(result.alarm_priority, Role.ALARM_P3)]) if alarm else trace
    readout = item.pvm.choices.get("show_readout", True)
    body = QRectF(rect.left() + 4, rect.top() + 6, rect.width() - 8,
                  rect.height() - (34 if readout else 12))
    ink = _svg_shell(painter, body, str(item.pvm.choices.get("equipment_symbol", "vessel")), palette=palette)
    if ink is None:
        painter.setPen(QPen(line, 1.4))
        painter.setBrush(QColor(palette[Role.EQUIPMENT_FILL]))
        painter.drawRoundedRect(body, 16, 16)
        ink = body
    cap = ink.height() * 15 / 200
    inner = ink.adjusted(5, cap + 2, -5, -(cap + 2))
    bar = QRectF(inner.right() - 15, inner.top(), 15, inner.height())
    trend = QRectF(inner.left(), inner.top(), max(1, bar.left() - inner.left()), inner.height())
    painter.save()
    painter.setClipRect(inner)
    painter.setPen(QPen(line, .8))
    painter.setBrush(QColor(palette[Role.SURFACE_SUNK]))
    painter.drawRect(bar)
    fraction = _fraction(value, eu) if eu and value is not None else None
    if fraction is not None:
        height = (bar.height() - 2) * fraction
        painter.setPen(Qt.NoPen)
        painter.setBrush(level)
        painter.drawRect(QRectF(bar.left() + 1, bar.bottom() - 1 - height, bar.width() - 2, height))
    painter.setPen(QPen(line, 1, Qt.DashLine))
    for _name, at in limit_fractions(item, eu):
        y = inner.bottom() - inner.height() * at
        painter.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))
    painter.setPen(QPen(QColor(palette[Role.LINE_SOFT]), .7))
    painter.drawLine(QPointF(trend.center().x(), trend.top()), QPointF(trend.center().x(), trend.bottom()))
    # Gaps stay gaps: never interpolate through Bad I/O or invent a one-hour
    # history from a single current value. The label names the actual buffer.
    history = tuple(item.history)
    painter.setPen(QPen(trace, 1.8))
    painter.setBrush(Qt.NoBrush)
    if eu and value is not None and len(history) >= 2:
        prior = None
        for index, sample in enumerate(history):
            if sample is None or not math.isfinite(sample):
                prior = None
                continue
            at = _fraction(sample, eu)
            point = QPointF(trend.left() + trend.width() * index / (len(history) - 1),
                            trend.bottom() - trend.height() * at)
            if prior is not None:
                painter.drawLine(prior, point)
            prior = point
    painter.restore()
    painter.setFont(item.typeface("tag", "Segoe UI", 7))
    painter.setPen(QColor(palette[Role.TEXT_DIM]))
    caption = "Recent samples" if len(history) >= 2 else "Collecting trend"
    if value is None:
        caption = "No good level data"
    painter.drawText(QRectF(ink.left(), inner.bottom() + 1, ink.width(), cap), Qt.AlignCenter, caption)
    if readout:
        label = item.pvm.label or str(next(iter(item.pvm.params.values()), "")).split("/")[-1]
        painter.setFont(item.typeface("tag", "Segoe UI", 8))
        painter.setPen(QColor(palette[Role.TEXT]))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 28, rect.width(), 13), Qt.AlignCenter, label)
        painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
        units = str(getattr(result, "units", "") or "")
        reading = "—" if value is None else f"{value:.1f} {units}".strip()
        painter.drawText(QRectF(rect.left(), rect.bottom() - 15, rect.width(), 13), Qt.AlignCenter, reading)
    if alarm:
        draw_alarm_badge(painter, rect.right() - 18, rect.top(), result.alarm_priority,
                         palette=palette, font=item.typeface("alarm", "Segoe UI", 8, QFont.Bold))
