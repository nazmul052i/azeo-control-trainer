"""The High Performance PVM faces, built from the shared bars.

Registered into `pvm_painters.CLASS_PAINTERS` as the `hp` variants.
They are **new variants rather than replacements**: the existing
`dynamo_inline` painters are what every shipped display already draws,
and quietly re-rendering them would change displays nobody asked to
change. An engineer opts a PVM into the HP face by picking the variant.

The layout is the manual's: identity above (drawn by the shared
anatomy, not here), then labelled data fields beside the combination
bar, then the OUT bar underneath. Labels sit left of their fields
because "Shows up to six right-justified digits" only buys a readable
column if the column starts in the same place on every PVM.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from ...theme.roles import Role
from .bars import (
    BarData, draw_combination_bar, draw_deviation_bar, draw_out_bar,
    resolve_scale,
)
from .tag import eu_descriptor, format_data_field

LABEL_W = 22.0
FIELD_W = 44.0
ROW_H = 13.0
BAR_H = 14.0


def _row_value(item, key):
    """One bound row's value, or None when the class did not bind it."""
    binding = item.rows.get(key)
    if binding is None:
        return None
    return binding.result.value


def _decimals(result):
    """F_DECPT, where the binding carries it."""
    places = getattr(result, "decimals", None)
    try:
        return int(places) if places is not None else None
    except (TypeError, ValueError):
        return None


def _field_row(painter, item, rect: QRectF, y: float, label: str, value,
               units: str, palette: dict, decimals=None) -> None:
    """`LABEL   1234.5 degC`, with the number in a fixed column."""
    painter.setFont(item.typeface("secondary", "Segoe UI", 7,
                                  QFont.Bold))
    painter.setPen(QColor(palette[Role.TEXT_DIM]))
    label_width = max(LABEL_W, painter.fontMetrics().horizontalAdvance("OUT") + 2)
    painter.drawText(QRectF(rect.left(), y, label_width, ROW_H),
                     Qt.AlignLeft | Qt.AlignVCenter, label)
    # Monospace so the digits line up column-wise between PVMs — a
    # proportional font undoes the fixed width the field just bought.
    painter.setFont(item.typeface("value", "Consolas", 8))
    painter.setPen(QColor(palette[Role.TEXT]))
    text = format_data_field(value, decimals)
    # Large typography previously retained a 44-unit field and silently
    # clipped leading digits (166.66 appeared as 66.66). Reserve one stable
    # measured numeric column for the selected class font, including signs.
    metrics = painter.fontMetrics()
    field_width = max(FIELD_W, metrics.horizontalAdvance("-888.88") + 2,
                      metrics.horizontalAdvance(text) + 2)
    painter.drawText(QRectF(rect.left() + label_width, y, field_width, ROW_H),
                     Qt.AlignRight | Qt.AlignVCenter,
                     text)
    if units:
        painter.setFont(item.typeface("secondary", "Segoe UI", 7))
        painter.setPen(QColor(palette[Role.TEXT_DIM]))
        painter.drawText(
            QRectF(rect.left() + label_width + field_width + 3, y,
                   max(0, rect.width() - label_width - field_width - 3), ROW_H),
            Qt.AlignLeft | Qt.AlignVCenter, units)


def _bar_data(item, result, show_sp: bool) -> BarData:
    return BarData(
        pv=_row_value(item, "PV"),
        sp=_row_value(item, "SP"),
        sp_wrk=_row_value(item, "SP_WRK"),
        lo=_row_value(item, "LO"), lo_lo=_row_value(item, "LL"),
        hi=_row_value(item, "HI"), hi_hi=_row_value(item, "HH"),
        scale=resolve_scale(result,
                            item.pvm.params.get("scale_lo"),
                            item.pvm.params.get("scale_hi")),
        show_sp=show_sp)


def _paint_hp(painter: QPainter, item, show_sp: bool,
              deviation: bool = False) -> None:
    rect = item.rect()
    palette = item._palette
    result = item.binding.result if item.binding else None
    units = eu_descriptor(result)
    places = _decimals(result)
    data = _bar_data(item, result, show_sp)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette[Role.SURFACE_PANEL]))
    painter.drawRect(rect)

    out = _row_value(item, "OUT")
    show_out = out is not None or show_sp
    rows = 1 + int(show_sp) + int(show_out)
    content_height = rows * (ROW_H + 1) + BAR_H + 3
    if show_out:
        content_height += BAR_H * 0.6
    # The shared anatomy owns the bottom 16 units. Previously OUT and its bar
    # painted through the status icons and beyond the class's 64-unit footprint.
    available = rect.adjusted(5, 4, -5, -18)
    if show_sp and available.width() >= 180 and available.height() >= 40:
        # Wide overview tiles have space for two columns. Stacking all five
        # rows here shrank otherwise readable values to six-pixel text.
        gap = 8.0
        half = (available.width() - gap) / 2.0
        left = QRectF(available.left(), available.top(), half, available.height())
        right = QRectF(left.right() + gap, left.top(), half, left.height())
        _field_row(painter, item, left, left.top(), "PV", data.pv, units, palette, places)
        bar = QRectF(right.left(), right.top() + 1, right.width(), ROW_H - 2)
        if deviation:
            draw_deviation_bar(painter, bar, data.pv, data.sp, palette,
                               scale=data.scale, span_percent=20.0)
        else:
            draw_combination_bar(painter, bar, data, palette)
        _field_row(painter, item, left, left.top() + ROW_H + 2, "SP", data.sp, units, palette, places)
        _field_row(painter, item, right, right.top() + ROW_H + 2, "OUT", out, "%", palette, 1)
        draw_out_bar(painter, QRectF(available.left(), available.top() + 2 * ROW_H + 5,
                                     available.width(), BAR_H * 0.6), out, palette)
        return
    scale = max(1e-6, min(1.0, available.height() / content_height))
    painter.save()
    painter.translate(available.topLeft())
    painter.scale(scale, scale)
    inner = QRectF(0, 0, available.width() / scale, content_height)
    y = 0.0
    _field_row(painter, item, inner, y, "PV", data.pv, units,
               palette, places)
    y += ROW_H + 1
    bar = QRectF(inner.left(), y, inner.width(), BAR_H)
    if deviation:
        draw_deviation_bar(painter, bar, data.pv, data.sp, palette,
                           scale=data.scale, span_percent=20.0)
    else:
        draw_combination_bar(painter, bar, data, palette)
    y += BAR_H + 3
    if show_sp:
        _field_row(painter, item, inner, y, "SP", data.sp, units, palette,
                   places)
        y += ROW_H + 1
    if show_out:
        _field_row(painter, item, inner, y, "OUT", out, "%", palette, 1)
        y += ROW_H + 1
        draw_out_bar(painter, QRectF(inner.left(), y, inner.width(),
                                     BAR_H * 0.6), out, palette)
    painter.restore()


def paint_hp_combination(painter: QPainter, item) -> None:
    """PID / ALM: PV, SP, the combination bar and OUT."""
    _paint_hp(painter, item, show_sp=True)


def paint_hp_indicator(painter: QPainter, item) -> None:
    """AI: the same face with the setpoint indicators hidden.

    "The SP and SP_WRK indicators are hidden when the function block
    definition is the AI block" — an indicator has no target, and
    drawing one would offer the operator something to chase.
    """
    _paint_hp(painter, item, show_sp=False)


def paint_hp_deviation(painter: QPainter, item) -> None:
    """PID with the deviation bar in place of the combination bar."""
    _paint_hp(painter, item, show_sp=True, deviation=True)


def paint_hp_vertical_indicator(painter: QPainter, item) -> None:
    """Vertical analog family, using the same bar resolver and marks."""
    rect = item.rect()
    result = item.binding.result if item.binding else None
    painter.fillRect(rect, QColor(item._palette[Role.SURFACE_PANEL]))
    inner = rect.adjusted(10, 5, -10, -5)
    data = _bar_data(item, result, show_sp=item.pvm.block_type == "PID")
    draw_combination_bar(painter, inner, data, item._palette, vertical=True)


def paint_hp_numeric(painter: QPainter, item) -> None:
    """Numeric-only installed family: fixed-width value, no false bar."""
    rect = item.rect()
    result = item.binding.result if item.binding else None
    painter.fillRect(rect, QColor(item._palette[Role.SURFACE_PANEL]))
    painter.setPen(QColor(item._palette[Role.TEXT]))
    painter.setFont(item.typeface("value", "Consolas", 10, QFont.Bold))
    painter.drawText(rect.adjusted(5, 4, -5, -4), Qt.AlignCenter,
                     format_data_field(
                         result.value if result is not None else None,
                         _decimals(result)))


def paint_hp_alarms(painter: QPainter, item) -> None:
    """HP_Alarms: the reference's label over alarm icon and count.

    The class is a compact single roll-up, not a three-column dashboard.
    An ACTIVE/UNACK/SUPPR row cannot fit its documented 116x44 footprint and
    collides with the shared alarm mark. Detailed states remain available in
    the alarm list and hover; this PVM shows the area's alarm count.
    """
    rows = tuple(item.alarm_provider() if item.alarm_provider else ())
    rect = item.rect()
    painter.fillRect(rect, QColor(item._palette[Role.SURFACE_PANEL]))
    painter.setFont(item.typeface("tag", "Segoe UI", 8, QFont.Bold))
    painter.setPen(QColor(item._palette[Role.TEXT]))
    painter.drawText(rect.adjusted(5, 2, -5, -22),
                     Qt.AlignLeft | Qt.AlignVCenter,
                     item.pvm.label or "ALARMS")
    painter.setFont(item.typeface("value", "Consolas", 10, QFont.Bold))
    painter.setPen(QColor(item._palette[
        Role.ALARM_P1 if rows else Role.TEXT_DIM]))
    # The shared anatomy owns the leftmost 16-unit alarm/status slot.
    painter.drawText(
        QRectF(rect.left() + 25, rect.bottom() - 21,
               rect.width() - 30, 18),
        Qt.AlignLeft | Qt.AlignVCenter, str(len(rows)))
