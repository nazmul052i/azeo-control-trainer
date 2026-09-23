"""The High Performance bar graphs.

Three of them, and each answers a different question at a glance:

- **Combination** — where is PV in its range, and where is SP relative
  to it? "This graphic information allows operators to scan a display
  and understand their approximate values and position in range without
  needing to read the corresponding numerical values."
- **Deviation** — how far is PV from SP, with SP pinned to the centre
  so the *distance* is the whole signal.
- **OUT** — how much authority has the controller spent.

**The shape recognition is the point of the combination bar.** "When PV
is equal to SP, the PV bar touches the perpendicular SP bar, forming a
'T'. If the PV is above SP, a 't' is shown." That is why SP is drawn as
a line across the bar and not as a second fill: a T is recognisable
from across a control room in a way that two similar bar lengths are
not. Keep the SP tick spanning past the bar's edges — a tick that stops
at the fill has no crossbar to read.

**Alarm limits are drawn subtly, on purpose.** The manual: "The
indication of these alarm locations is shown subtly (such as in grays),
providing alarm limit information without being distracting or creating
excessive visual clutter." Ours keep the priority's hue at low alpha —
subtle as asked, and still the same colour vocabulary the banner and
the alarm marks use, so a limit region cannot imply a priority the
alarm system disagrees with.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen

from ...theme.roles import Role

#: Alpha for the alarm limit regions — "shown subtly (such as in
#: grays)". Low enough not to compete with the PV bar, high enough to
#: locate the limit.
LIMIT_ALPHA = 70
#: The SP tick overhangs the bar by this much at each end, so PV
#: meeting SP reads as a T rather than as a bar with a stripe.
SP_OVERHANG = 2.0
SP_WIDTH = 2.0
#: The alarm limit regions live in a thin strip along the bar's far
#: edge rather than behind the PV bar. Drawn behind it they are
#: invisible for exactly the values that matter — a PV down at LOLO
#: covers the LOLO region it is sitting in — so the operator loses the
#: limit at the moment they need it. This fraction is the strip's
#: share of the track height.
LIMIT_STRIP = 0.30
#: OUT bar ticks, per the manual: "the 25, 50, and 75 mark".
OUT_TICKS = (0.25, 0.50, 0.75)


@dataclass(frozen=True)
class BarScale:
    """The range a bar is drawn against.

    `user_defined` is not decoration: "When user-defined scales are
    configured for the PVM class, perpendicular lines are shown at the
    end of the combination bar graph, indicating that the scale is
    user-defined." Without that mark an operator reads a partial range
    as the full one and misjudges every value on the display.
    """

    lo: float = 0.0
    hi: float = 100.0
    user_defined: bool = False

    @property
    def span(self) -> float:
        return float(self.hi) - float(self.lo)

    def fraction(self, value) -> float | None:
        """Value -> 0..1 along the bar, or None when it is not a number.

        None rather than 0.0, because a bar drawn at the bottom of its
        range is a claim about the process and an unreadable value is
        not one.
        """
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not self.span:
            return None
        return max(0.0, min(1.0, (number - float(self.lo)) / self.span))


def resolve_scale(result=None, lo=None, hi=None) -> BarScale:
    """The scale to draw against.

    User-defined values win; otherwise the binding's own EU range
    (PV_SCALE, or OUT_SCALE for an AI block); otherwise 0-100.
    """
    if lo is not None and hi is not None:
        try:
            return BarScale(float(lo), float(hi), user_defined=True)
        except (TypeError, ValueError):
            pass
    eu = getattr(result, "eu_range", None) if result is not None else None
    if eu and len(eu) >= 2:
        try:
            return BarScale(float(eu[0]), float(eu[1]))
        except (TypeError, ValueError):
            pass
    return BarScale()


@dataclass
class BarData:
    """What one combination bar draws."""

    pv: object = None
    sp: object = None
    sp_wrk: object = None
    lo: object = None
    lo_lo: object = None
    hi: object = None
    hi_hi: object = None
    scale: BarScale = BarScale()
    #: "The SP and SP_WRK indicators are hidden when the function block
    #: definition is the AI block" — an indicator has no setpoint, and
    #: drawing one invents a target the operator cannot act on.
    show_sp: bool = True


def _along(rect: QRectF, fraction: float, vertical: bool) -> float:
    """A fraction's pixel position along the bar's long axis."""
    if vertical:
        return rect.bottom() - fraction * rect.height()
    return rect.left() + fraction * rect.width()


def _fill_from_origin(rect: QRectF, fraction: float,
                      vertical: bool) -> QRectF:
    """The rectangle from the bar's origin end out to `fraction`."""
    if vertical:
        top = rect.bottom() - fraction * rect.height()
        return QRectF(rect.left(), top, rect.width(),
                      rect.bottom() - top)
    return QRectF(rect.left(), rect.top(),
                  fraction * rect.width(), rect.height())


def _fill_from_far(rect: QRectF, fraction: float,
                   vertical: bool) -> QRectF:
    """The rectangle from `fraction` out to the bar's far end."""
    if vertical:
        top = rect.top()
        bottom = rect.bottom() - fraction * rect.height()
        return QRectF(rect.left(), top, rect.width(), bottom - top)
    left = rect.left() + fraction * rect.width()
    return QRectF(left, rect.top(), rect.right() - left, rect.height())


def limit_strip(rect: QRectF, vertical: bool) -> QRectF:
    """The band the alarm limit regions occupy — far edge of the track."""
    if vertical:
        width = rect.width() * LIMIT_STRIP
        return QRectF(rect.right() - width, rect.top(), width,
                      rect.height())
    height = rect.height() * LIMIT_STRIP
    return QRectF(rect.left(), rect.bottom() - height, rect.width(),
                  height)


def value_strip(rect: QRectF, vertical: bool) -> QRectF:
    """The band the PV bar occupies — everything the limits leave."""
    if vertical:
        return QRectF(rect.left(), rect.top(),
                      rect.width() * (1.0 - LIMIT_STRIP), rect.height())
    return QRectF(rect.left(), rect.top(), rect.width(),
                  rect.height() * (1.0 - LIMIT_STRIP))


def _limit_regions(rect, data, vertical, painter, palette):
    """LO/LOLO from the origin end, HI/HIHI from the far end.

    "uses Alarm 1 color for HI and LO. uses Alarm 2 color for HIHI and
    LOLO." Ours reads that as priority order rather than as literal
    colour indices — HIHI/LOLO are the more serious pair, so they take
    the more serious role.
    """
    painter.setPen(Qt.NoPen)
    for value, role, from_origin in (
            (data.lo_lo, Role.ALARM_P1, True),
            (data.lo, Role.ALARM_P2, True),
            (data.hi, Role.ALARM_P2, False),
            (data.hi_hi, Role.ALARM_P1, False)):
        fraction = data.scale.fraction(value)
        if fraction is None:
            continue
        colour = QColor(palette[role])
        colour.setAlpha(LIMIT_ALPHA)
        painter.setBrush(colour)
        band = (_fill_from_origin(rect, fraction, vertical) if from_origin
                else _fill_from_far(rect, fraction, vertical))
        if band.width() > 0 and band.height() > 0:
            painter.drawRect(band)


def _sp_tick(painter, rect, fraction, colour, vertical):
    """The perpendicular SP indicator — the crossbar of the T."""
    painter.setPen(QPen(colour, SP_WIDTH))
    position = _along(rect, fraction, vertical)
    if vertical:
        painter.drawLine(QPointF(rect.left() - SP_OVERHANG, position),
                         QPointF(rect.right() + SP_OVERHANG, position))
    else:
        painter.drawLine(QPointF(position, rect.top() - SP_OVERHANG),
                         QPointF(position, rect.bottom() + SP_OVERHANG))


def draw_combination_bar(painter, rect: QRectF, data: BarData,
                         palette: dict, vertical: bool = False) -> None:
    """PV, SP, SP_WRK and the alarm limits, on one scale."""
    painter.setRenderHint(painter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette[Role.SURFACE_SUNK]))
    painter.drawRect(rect)
    _limit_regions(limit_strip(rect, vertical), data, vertical,
                   painter, palette)

    bar_rect = value_strip(rect, vertical)
    pv_fraction = data.scale.fraction(data.pv)
    if pv_fraction is not None:
        # Fill plus outline. The fill alone is Azeo's own bar colour
        # and it fails WCAG 1.4.11 against three of the four theme
        # grounds; the outline is what carries the edge, and deleting
        # it as redundant is the regression this comment exists to
        # prevent (`tests/_smoke_operator.py` asserts the bare fill
        # fails, so the outline cannot be quietly dropped).
        bar = _fill_from_origin(bar_rect, pv_fraction, vertical)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(palette[Role.BAR_PV]))
        painter.drawRect(bar)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(palette[Role.TEXT]), 1.0))
        painter.drawRect(bar)

    if data.show_sp:
        sp_fraction = data.scale.fraction(data.sp)
        wrk_fraction = data.scale.fraction(data.sp_wrk)
        # "When SP_WRK is different from SP, it is also shown on the
        # bar graph" — and only then. A working setpoint sitting on the
        # target is not news, and a second tick on top of the first is
        # a thicker line that means nothing.
        if (wrk_fraction is not None and sp_fraction is not None
                and abs(wrk_fraction - sp_fraction) > 1e-6):
            _sp_tick(painter, rect, wrk_fraction,
                     QColor(palette[Role.ACTION]), vertical)
        if sp_fraction is not None:
            _sp_tick(painter, rect, sp_fraction,
                     QColor(palette[Role.TEXT]), vertical)

    if data.scale.user_defined:
        _end_lines(painter, rect, palette, vertical)


def _end_lines(painter, rect: QRectF, palette: dict, vertical: bool):
    """Perpendicular lines at both ends: this scale is partial.

    "Perpendicular lines are shown at both ends of the bar graph to
    indicate that a partial range is defined."

    Drawn LAST and overhanging further than the SP tick, because the
    origin-end line sits exactly where the PV bar starts — at normal
    weight it disappears into the bar's own edge, and a partial scale
    that does not announce itself is read as a full one.
    """
    painter.setPen(QPen(QColor(palette[Role.TEXT]), 2.0))
    for fraction in (0.0, 1.0):
        position = _along(rect, fraction, vertical)
        over = SP_OVERHANG * 2
        if vertical:
            painter.drawLine(QPointF(rect.left() - over, position),
                             QPointF(rect.right() + over, position))
        else:
            painter.drawLine(QPointF(position, rect.top() - over),
                             QPointF(position, rect.bottom() + over))


def draw_out_bar(painter, rect: QRectF, value, palette: dict,
                 scale: BarScale | None = None) -> None:
    """OUT over OUT_SCALE, filling left to right, ticks at 25/50/75."""
    scale = scale or BarScale()
    painter.setRenderHint(painter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette[Role.SURFACE_SUNK]))
    painter.drawRect(rect)
    fraction = scale.fraction(value)
    if fraction is not None:
        bar = _fill_from_origin(rect, fraction, False)
        painter.setBrush(QColor(palette[Role.BAR_OUT]))
        painter.drawRect(bar)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(palette[Role.TEXT]), 1.0))
        painter.drawRect(bar)
    # Ticks span the full height and overhang below. A 3 px stub at
    # the foot of the bar is invisible once a fill covers it, which is
    # every reading above 25 % — the marks exist to be read AGAINST
    # the fill, so they have to survive it.
    painter.setPen(QPen(QColor(palette[Role.TEXT_DIM]), 1.0))
    for tick in OUT_TICKS:
        x = _along(rect, tick, False)
        painter.drawLine(QPointF(x, rect.top()),
                         QPointF(x, rect.bottom() + 2))


def deviation_fraction(pv, sp, scale: BarScale,
                       span_percent: float = 100.0) -> float | None:
    """Where the PV diamond sits, 0..1, with SP pinned at 0.5.

    "The distance between PV and SP on a bar graph always represents
    the same amount of deviation from that SP, whether PV is above or
    below SP" — so the axis is the deviation itself, not the value.
    `span_percent` of 10 means +/-5% of EU range end to end.
    """
    try:
        pv_value, sp_value = float(pv), float(sp)
    except (TypeError, ValueError):
        return None
    half = abs(scale.span) * (span_percent / 100.0) / 2.0
    if not half:
        return None
    return max(0.0, min(1.0, 0.5 + (pv_value - sp_value) / (2 * half)))


def draw_deviation_bar(painter, rect: QRectF, pv, sp, palette: dict,
                       scale: BarScale | None = None,
                       span_percent: float = 100.0,
                       vertical: bool = False) -> None:
    """PV against a centred SP. The diamond earns its visibility.

    "The greater the deviation, the more visible the PV diamond
    becomes" — a loop sitting on setpoint should be quiet, so at zero
    deviation the diamond is nearly the ground it sits on and only a
    real excursion pulls the eye.
    """
    scale = scale or BarScale()
    painter.setRenderHint(painter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette[Role.SURFACE_SUNK]))
    painter.drawRect(rect)
    # SP never moves: it is the reference the whole graph is about.
    _sp_tick(painter, rect, 0.5, QColor(palette[Role.TEXT]), vertical)
    fraction = deviation_fraction(pv, sp, scale, span_percent)
    if fraction is None:
        return
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    colour = QColor(palette[Role.BAR_PV])
    colour.setAlpha(int(90 + 165 * min(1.0, abs(fraction - 0.5) * 2)))
    painter.setBrush(colour)
    painter.setPen(QPen(QColor(palette[Role.TEXT]), 1.0))
    position = _along(rect, fraction, vertical)
    size = (rect.height() if not vertical else rect.width()) / 2.0
    cx = position if not vertical else rect.center().x()
    cy = rect.center().y() if not vertical else position
    painter.drawPolygon([QPointF(cx, cy - size), QPointF(cx + size, cy),
                         QPointF(cx, cy + size), QPointF(cx - size, cy)])
