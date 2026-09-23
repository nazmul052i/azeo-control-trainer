"""Custom canvas painters for dynamo PVMs — bar, tank trend, badges.

Keyed by (block_type, role, variant); `PvmItem.paint` consults this
registry before falling back to the generic value card. Each painter
receives the live item (bindings, history deque, palette) and draws to
the item's rect — layout only, state comes resolved (I6).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from ..theme.roles import Role

#: Every colour here comes from the active theme, resolved per item.
#: These were seven hex literals, which meant the custom dynamos —
#: the bars, the tanks, the valves, the most visible objects on the
#: display — kept their light-theme ink while the pipes around them
#: rethemed. A painter names a MEANING and the theme answers.
_INK = {
    "bar": Role.BAR_PV,        # the measurement bar
    "cap": Role.LINE,          # bar caps and tick marks
    "track": Role.SURFACE_SUNK,  # the empty part of a bar
    "value": Role.HEADING,     # the numeric readout
    "alarm": Role.ALARM_P1,
    "warn": Role.ALARM_P2,
    "line": Role.LINE,         # outlines and rules
    "shell": Role.EQUIPMENT_FILL,  # a vessel body with no artwork
}


def themed(item, name: str) -> QColor:
    """One named ink for the item being painted."""
    return QColor(item._palette[_INK[name]])


def _value(item, key, default=None):
    binding = item.rows.get(key)
    if binding is None:
        return default
    value = binding.result.value
    return default if value is None else value


def _fraction(value, eu_range):
    lo, hi = (eu_range or (0.0, 100.0))
    if value is None or hi <= lo:
        return None
    try:
        return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))
    except (TypeError, ValueError):
        return None


def _trend_samples(item, current) -> list:
    """History, or an honest flat trace while the first samples collect."""
    samples = list(item.history)
    if len(samples) >= 2 or current is None:
        return samples
    if samples:
        return [samples[-1], current]
    return [current, current]


def draw_alarm_badge(painter: QPainter, x: float, y: float,
                     priority: int, text: str = "",
                     palette: dict | None = None,
                     font: QFont | None = None) -> None:
    """The PlantPAx alarm marker: red square for critical, amber
    triangle for warning — shape first, colour second.

    Takes the palette rather than an item because the badge is also
    drawn by the generic card. The FILLS are theme-invariant (a
    priority is one colour everywhere), so only the outline and the
    numeral need the theme; without a palette they fall back to the
    invariant pair, which is right for every light theme.
    """
    def tone(role: Role, fallback: str) -> QColor:
        return QColor(palette[role]) if palette else QColor(fallback)

    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(tone(Role.LINE, "#3A3E42"), 1.0))
    if priority >= 15:
        painter.setBrush(tone(Role.ALARM_P1, "#C0392B"))
        painter.drawRect(QRectF(x, y, 16, 16))
        colour = "#FFFFFF"
    else:
        painter.setBrush(tone(Role.ALARM_P2, "#E0A020"))
        painter.drawPolygon([QPointF(x + 8, y),
                             QPointF(x, y + 15),
                             QPointF(x + 16, y + 15)])
        colour = "#1F2226"
    painter.setPen(QColor(colour))
    painter.setFont(font or QFont("Segoe UI", 8, QFont.Bold))
    painter.drawText(QRectF(x, y + (1 if priority < 15 else 0), 16, 16),
                     Qt.AlignCenter, text or "1")


def paint_analog_bar(painter: QPainter, item) -> None:
    rect = item.rect()
    result = item.binding.result if item.binding else None
    bad = result is not None and result.quality.name == "BAD"
    eu = result.eu_range if result is not None else None
    value = result.value if result is not None else None

    bar = QRectF(rect.center().x() - 8, rect.top() + 6,
                 16, rect.height() - 26)
    painter.setPen(QPen(themed(item, "line"), 1.0))
    painter.setBrush(themed(item, "track"))
    painter.drawRect(bar)
    # Dark caps.
    painter.setPen(Qt.NoPen)
    painter.setBrush(themed(item, "cap"))
    painter.drawRect(QRectF(bar.left(), bar.top(), bar.width(), 12))
    painter.drawRect(QRectF(bar.left(), bar.bottom() - 12,
                            bar.width(), 12))
    # Normal band between L and H (fallback: middle third).
    lo = _value(item, "LO")
    hi = _value(item, "HI")
    lo_f = _fraction(lo, eu) if lo is not None else 0.33
    hi_f = _fraction(hi, eu) if hi is not None else 0.66
    if lo_f is not None and hi_f is not None and hi_f > lo_f:
        top = bar.bottom() - bar.height() * hi_f
        painter.setBrush(themed(item, "bar"))
        painter.drawRect(QRectF(bar.left() + 1, top, bar.width() - 2,
                                bar.height() * (hi_f - lo_f)))
    # Alarm segment at the breached end.
    if result is not None and result.alarm_active:
        red_h = 10.0
        y = bar.top() + 12 if "HI" in (result.alarm_condition or "") \
            else bar.bottom() - 12 - red_h
        painter.setBrush(themed(item, "alarm"))
        painter.drawRect(QRectF(bar.left() + 1, y, bar.width() - 2,
                                red_h))
        painter.setBrush(themed(item, "warn"))
        offset = red_h if "HI" in (result.alarm_condition or "") \
            else -6
        painter.drawRect(QRectF(bar.left() + 1, y + offset,
                                bar.width() - 2, 5))
    # Pointer + value.
    fraction = None if bad else _fraction(value, eu)
    if fraction is not None:
        y = bar.bottom() - bar.height() * fraction
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(themed(item, "line"), 1.0))
        painter.setBrush(themed(item, "cap"))
        painter.drawPolygon([QPointF(bar.left() - 4, y),
                             QPointF(bar.left() - 14, y - 6),
                             QPointF(bar.left() - 14, y + 6)])
    painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
    painter.setPen(themed(item, "value"))
    text = "– – –" if bad or value is None else f"{float(value):g}"
    painter.drawText(QRectF(rect.left(), rect.bottom() - 16,
                            rect.width(), 14), Qt.AlignCenter, text)
    if result is not None and result.forced:
        painter.setPen(themed(item, "alarm"))
        painter.setFont(item.typeface("alarm", "Segoe UI", 6,
                                      QFont.Bold))
        painter.drawText(QRectF(rect.left(), rect.top(), rect.width(),
                                10), Qt.AlignCenter, "FORCED")


def _svg_shell(painter: QPainter, body: QRectF, name: str, *, palette=None):
    """Render the vendored P&ID silhouette stretched into `body` and
    return its ink rectangle (the drawable interior, in body
    coordinates) — the trainer's tank IS the course's tank symbol,
    not a second drawing of one. Returns None when the artwork is
    missing so the caller can fall back to painted geometry."""
    from .symbols import renderer
    from ..theme.roles import Role
    svg = (renderer(name, line=palette[Role.EQUIPMENT], fill=palette[Role.EQUIPMENT_FILL])
           if palette is not None else renderer(name))
    if svg is None:
        return None
    view = svg.viewBoxF()
    if view.width() <= 0 or view.height() <= 0:
        return None
    painter.setRenderHint(QPainter.Antialiasing, True)
    svg.render(painter, body)
    # The pid/ set draws its shape inside a 4-unit margin.
    fx, fy = 4.0 / view.width(), 4.0 / view.height()
    return QRectF(body.left() + body.width() * fx,
                  body.top() + body.height() * fy,
                  body.width() * (1 - 2 * fx),
                  body.height() * (1 - 2 * fy))


def paint_tank_trend(painter: QPainter, item) -> None:
    rect = item.rect()
    result = item.binding.result if item.binding else None
    bad = result is not None and result.quality.name == "BAD"
    eu = result.eu_range if result is not None else None
    value = result.value if result is not None else None
    show_readout = item.pvm.choices.get("show_readout", True)

    body = QRectF(rect.left() + 4, rect.top() + 6,
                  rect.width() - 8,
                  rect.height() - (34 if show_readout else 12))
    ink = _svg_shell(painter, body, "tank")
    if ink is None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(themed(item, "line"), 1.4))
        painter.setBrush(themed(item, "shell"))
        painter.drawRoundedRect(body, 14, 14)
        ink = body
    inner = ink.adjusted(2, 2, -2, -2)
    painter.setClipRect(inner)
    # Level fill from the bottom.
    fraction = None if bad else _fraction(value, eu)
    if fraction is not None:
        fill_h = inner.height() * fraction
        painter.setPen(Qt.NoPen)
        painter.setBrush(themed(item, "bar"))
        painter.drawRect(QRectF(inner.left(),
                                inner.bottom() - fill_h,
                                inner.width(), fill_h))
    # The sparkline: recent history across the vessel.
    history = _trend_samples(item, value)
    if len(history) >= 2 and eu and not bad:
        lo, hi = eu
        span = max(hi - lo, 1e-9)
        step = inner.width() / max(len(history) - 1, 1)
        points = []
        for i, sample in enumerate(history):
            try:
                f = max(0.0, min(1.0, (float(sample) - lo) / span))
            except (TypeError, ValueError):
                continue
            points.append(QPointF(inner.left() + i * step,
                                  inner.bottom() - inner.height() * f))
        if len(points) >= 2:
            painter.setPen(QPen(themed(item, "line"), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(points)
            painter.setPen(Qt.NoPen)
            painter.setBrush(themed(item, "alarm"))
            painter.drawEllipse(points[-1], 2.5, 2.5)
    painter.setClipping(False)

    if show_readout:
        tag = "/".join(str(v) for v in item.pvm.params.values())
        tag = tag.split("/")[-1] if "/" in tag else tag
        painter.setFont(item.typeface("tag", "Segoe UI", 8))
        painter.setPen(themed(item, "line"))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 28,
                                rect.width(), 13), Qt.AlignCenter, tag)
        painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
        painter.setPen(themed(item, "value"))
        text = "– – –" if bad or value is None else f"{float(value):g}"
        painter.drawText(QRectF(rect.left(), rect.bottom() - 15,
                                rect.width(), 13), Qt.AlignCenter, text)
    if result is not None and result.alarm_active:
        draw_alarm_badge(painter, rect.center().x() - 8, rect.top(),
                         result.alarm_priority,
                         palette=item._palette,
                         font=item.typeface("alarm", "Segoe UI", 8,
                                            QFont.Bold))


def paint_valve_op(painter: QPainter, item) -> None:
    """The control-valve silhouette with the OP bar under it — how
    open the controller has driven it, at a glance."""
    from .symbols import renderer

    rect = item.rect()
    result = item.binding.result if item.binding else None
    bad = result is not None and result.quality.name == "BAD"
    eu = result.eu_range if result is not None else (0.0, 100.0)
    value = result.value if result is not None else None

    symbol_rect = QRectF(rect.left(), rect.top(),
                         rect.width(), rect.height() - 40)
    svg = renderer("control_valve")
    if svg is not None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        view = svg.viewBoxF()
        scale = min(symbol_rect.width() / max(view.width(), 1),
                    symbol_rect.height() / max(view.height(), 1))
        w, h = view.width() * scale, view.height() * scale
        target = QRectF(symbol_rect.center().x() - w / 2,
                        symbol_rect.center().y() - h / 2, w, h)
        painter.setOpacity(0.45 if bad else 1.0)
        svg.render(painter, target)
        painter.setOpacity(1.0)

    # The OP bar: track, fill to the fraction, readback tick.
    bar = QRectF(rect.left() + 6, rect.bottom() - 36,
                 rect.width() - 12, 10)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(QPen(themed(item, "line"), 1.0))
    painter.setBrush(themed(item, "track"))
    painter.drawRect(bar)
    fraction = None if bad else _fraction(value, eu)
    if fraction is not None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(themed(item, "bar"))
        painter.drawRect(QRectF(bar.left() + 1, bar.top() + 1,
                                (bar.width() - 2) * fraction,
                                bar.height() - 2))
    readback = _value(item, "READBACK")
    rb_f = None if bad else _fraction(readback, eu)
    if rb_f is not None:
        x = bar.left() + 1 + (bar.width() - 2) * rb_f
        painter.setPen(QPen(themed(item, "line"), 2.0))
        painter.drawLine(QPointF(x, bar.top() - 2),
                         QPointF(x, bar.bottom() + 2))

    tag = "/".join(str(v) for v in item.pvm.params.values())
    tag = tag.split("/")[-1] if "/" in tag else tag
    tag = item.pvm.label or tag
    painter.setFont(item.typeface("tag", "Consolas", 8, QFont.Bold))
    painter.setPen(themed(item, "line"))
    painter.drawText(QRectF(rect.left(), rect.bottom() - 24,
                            rect.width(), 12), Qt.AlignCenter, tag)
    painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
    painter.setPen(themed(item, "value"))
    text = "– – –" if bad or value is None \
        else f"{float(value):.1f} %"
    painter.drawText(QRectF(rect.left(), rect.bottom() - 12,
                            rect.width(), 12), Qt.AlignCenter, text)
    if result is not None and result.alarm_active:
        draw_alarm_badge(painter, rect.right() - 16, rect.top(),
                         result.alarm_priority,
                         palette=item._palette,
                         font=item.typeface("alarm", "Segoe UI", 8,
                                            QFont.Bold))
    if result is not None and result.forced:
        painter.setPen(themed(item, "alarm"))
        painter.setFont(item.typeface("alarm", "Segoe UI", 6,
                                      QFont.Bold))
        painter.drawText(QRectF(rect.left(), rect.top(),
                                rect.width(), 10),
                         Qt.AlignCenter, "FORCED")


def paint_vessel_trend(painter: QPainter, item) -> None:
    """One shared anatomy for the installed AI and PID vessel PVMs."""
    from .vessel_trend import paint
    paint(painter, item)


def _tag_of(item) -> str:
    tag = "/".join(str(v) for v in item.pvm.params.values())
    return item.pvm.label or (tag.split("/")[-1] if "/" in tag
                              else tag)


def paint_horizontal_bar(painter: QPainter, item) -> None:
    """AnalogBarPvm's vocabulary, horizontal: track, caps, normal
    band between L and H, pointer beneath, value right — reading
    left to right beside a pipe run."""
    rect = item.rect()
    result = item.binding.result if item.binding else None
    bad = result is not None and result.quality.name == "BAD"
    eu = result.eu_range if result is not None else None
    value = result.value if result is not None else None

    bar = QRectF(rect.left() + 6, rect.top() + 8,
                 rect.width() - 12, 14)
    painter.setPen(QPen(themed(item, "line"), 1.0))
    painter.setBrush(themed(item, "track"))
    painter.drawRect(bar)
    painter.setPen(Qt.NoPen)
    painter.setBrush(themed(item, "cap"))
    painter.drawRect(QRectF(bar.left(), bar.top(), 10, bar.height()))
    painter.drawRect(QRectF(bar.right() - 10, bar.top(), 10,
                            bar.height()))
    lo = _value(item, "LO")
    hi = _value(item, "HI")
    lo_f = _fraction(lo, eu) if lo is not None else 0.33
    hi_f = _fraction(hi, eu) if hi is not None else 0.66
    if lo_f is not None and hi_f is not None and hi_f > lo_f:
        painter.setBrush(themed(item, "bar"))
        painter.drawRect(QRectF(bar.left() + bar.width() * lo_f,
                                bar.top() + 1,
                                bar.width() * (hi_f - lo_f),
                                bar.height() - 2))
    fraction = None if bad else _fraction(value, eu)
    if fraction is not None:
        x = bar.left() + bar.width() * fraction
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(themed(item, "line"), 1.0))
        painter.setBrush(themed(item, "cap"))
        painter.drawPolygon([QPointF(x, bar.bottom() + 3),
                             QPointF(x - 6, bar.bottom() + 12),
                             QPointF(x + 6, bar.bottom() + 12)])
    painter.setFont(item.typeface("tag", "Segoe UI", 7))
    painter.setPen(QColor(item._palette[Role.TEXT_DIM]))
    painter.drawText(QRectF(rect.left() + 6, rect.bottom() - 15,
                            rect.width() * 0.5, 13),
                     Qt.AlignLeft | Qt.AlignVCenter, _tag_of(item))
    painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
    painter.setPen(themed(item, "value"))
    text = "– – –" if bad or value is None else f"{float(value):g}"
    painter.drawText(QRectF(rect.left(), rect.bottom() - 16,
                            rect.width() - 6, 14),
                     Qt.AlignRight | Qt.AlignVCenter, text)
    if result is not None and result.alarm_active:
        draw_alarm_badge(painter, rect.right() - 16, rect.top(),
                         result.alarm_priority,
                         palette=item._palette,
                         font=item.typeface("alarm", "Segoe UI", 8,
                                            QFont.Bold))


def paint_reactor_bar(painter: QPainter, item) -> None:
    """The reactor silhouette with its measurement as a vertical bar
    beside the shell — equipment and number read together."""
    from .symbols import renderer

    rect = item.rect()
    result = item.binding.result if item.binding else None
    bad = result is not None and result.quality.name == "BAD"
    eu = result.eu_range if result is not None else None
    value = result.value if result is not None else None

    symbol_rect = QRectF(rect.left(), rect.top() + 2,
                         rect.width() - 34, rect.height() - 20)
    svg = renderer("reactor")
    if svg is not None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        view = svg.viewBoxF()
        scale = min(symbol_rect.width() / max(view.width(), 1),
                    symbol_rect.height() / max(view.height(), 1))
        w, h = view.width() * scale, view.height() * scale
        painter.setOpacity(0.45 if bad else 1.0)
        svg.render(painter, QRectF(
            symbol_rect.center().x() - w / 2,
            symbol_rect.center().y() - h / 2, w, h))
        painter.setOpacity(1.0)
    bar = QRectF(rect.right() - 24, rect.top() + 8, 14,
                 rect.height() - 34)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(QPen(themed(item, "line"), 1.0))
    painter.setBrush(themed(item, "track"))
    painter.drawRect(bar)
    lo = _value(item, "LO")
    hi = _value(item, "HI")
    lo_f = _fraction(lo, eu) if lo is not None else 0.33
    hi_f = _fraction(hi, eu) if hi is not None else 0.66
    if lo_f is not None and hi_f is not None and hi_f > lo_f:
        top = bar.bottom() - bar.height() * hi_f
        painter.setPen(Qt.NoPen)
        painter.setBrush(themed(item, "bar"))
        painter.drawRect(QRectF(bar.left() + 1, top, bar.width() - 2,
                                bar.height() * (hi_f - lo_f)))
    fraction = None if bad else _fraction(value, eu)
    if fraction is not None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(themed(item, "cap"))
        fill_h = bar.height() * fraction
        painter.drawRect(QRectF(bar.left() + 3,
                                bar.bottom() - fill_h,
                                bar.width() - 6, fill_h))
    painter.setPen(QPen(themed(item, "line"), 1.0))
    for f in (lo_f, hi_f):
        if f is not None:
            y = bar.bottom() - bar.height() * f
            painter.drawLine(QPointF(bar.right(), y),
                             QPointF(bar.right() + 4, y))
    painter.setFont(item.typeface("tag", "Segoe UI", 7))
    painter.setPen(themed(item, "line"))
    painter.drawText(QRectF(rect.left(), rect.bottom() - 16,
                            rect.width() * 0.6, 14),
                     Qt.AlignLeft | Qt.AlignVCenter, _tag_of(item))
    painter.setFont(item.typeface("value", "Segoe UI", 8, QFont.Bold))
    painter.setPen(themed(item, "value"))
    text = "– – –" if bad or value is None else f"{float(value):g}"
    painter.drawText(QRectF(rect.left(), rect.bottom() - 16,
                            rect.width(), 14),
                     Qt.AlignRight | Qt.AlignVCenter, text)
    if result is not None and result.alarm_active:
        draw_alarm_badge(painter, rect.left(), rect.top(),
                         result.alarm_priority,
                         palette=item._palette,
                         font=item.typeface("alarm", "Segoe UI", 8,
                                            QFont.Bold))


def paint_machine(painter, item):
    """Speed and run status remain separate evidence on the process drawing."""
    from .symbols import renderer
    from ..theme.roles import Role

    rect = item.rect()
    result, state = item._state()
    symbol = renderer(item._symbol_name(), line=item._palette[Role.EQUIPMENT],
                      fill=item._palette[Role.EQUIPMENT_FILL], text=item._palette[Role.TEXT])
    if symbol is not None:
        target = item._symbol_view_rect()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.translate(target.center())
        painter.rotate(item.pvm.rot or 0)
        symbol.render(painter, QRectF(-target.width() / 2, -target.height() / 2,
                                      target.width(), target.height()))
        painter.restore()

    label, reading, run_text, fault = item._machine_readout
    rows = ("" if item.pvm.choices.get("show_hp_anatomy", True) else label,
            reading, run_text)
    for index, text in enumerate(rows):
        painter.setFont(item.typeface("tag" if index == 0 else "secondary", "Segoe UI", 8))
        painter.setPen(item.colour(Role.ALARM_P1_TEXT if fault and index == 2 else Role.TEXT))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 48 + index * 16,
                                rect.width(), 16), Qt.AlignCenter, text)
    if fault or (state is not None and state.name == "alarm"):
        draw_alarm_badge(painter, rect.right() - 16, rect.top(), 15, "1", palette=item._palette)


#: (block_type, role, variant) -> painter(painter, item)
# The High Performance faces are NEW VARIANTS, never replacements for
# the painters above: those are what every shipped display already
# draws, and re-rendering them under the same key would change displays
# nobody asked to change. An engineer opts in by picking the variant.
from .hp.painters import (                                    # noqa: E402
    paint_hp_alarms, paint_hp_combination, paint_hp_deviation,
    paint_hp_indicator, paint_hp_numeric, paint_hp_vertical_indicator,
)
from .fan_indicator import paint as paint_fan_indicator       # noqa: E402

CLASS_PAINTERS = {
    ("AI", "dynamo_inline", "fan_indicator"): paint_fan_indicator,
    ("PID", "dynamo_inline", "vfd"): paint_machine,
    ("PID", "dynamo_inline", "turbine_speed"): paint_machine,
    ("PID", "dynamo_inline", "compressor_speed"): paint_machine,
    ("AI", "dynamo_inline", ""): paint_analog_bar,
    ("AI", "dynamo_inline", "hp"): paint_hp_indicator,
    ("PID", "dynamo_inline", "hp"): paint_hp_combination,
    ("PID", "dynamo_inline", "hp_deviation"): paint_hp_deviation,
    ("AI", "dynamo_inline", "tank"): paint_tank_trend,
    ("AI", "dynamo_inline", "vessel"): paint_vessel_trend,
    ("PID", "dynamo_inline", "vessel"): paint_vessel_trend,
    ("AI", "dynamo_inline", "hbar"): paint_horizontal_bar,
    ("AI", "dynamo_inline", "reactor"): paint_reactor_bar,
    ("AO", "dynamo_inline", "valve"): paint_valve_op,
}

# The installed High Performance catalogue shares painters by anatomy;
# class identity still controls the exact measured footprint.
from .hp.classes import HP_CLASS_KEYS                         # noqa: E402
for _installed, _key in HP_CLASS_KEYS.items():
    if _installed == "HP_Alarms":
        CLASS_PAINTERS[_key] = paint_hp_alarms
    elif _key[0] == "PID":
        CLASS_PAINTERS[_key] = (
            paint_hp_deviation if "_D" in _installed
            else paint_hp_numeric if "_N_" in _installed
            else paint_hp_combination)
    elif _key[0] == "AI":
        CLASS_PAINTERS[_key] = (
            paint_hp_vertical_indicator if ("_CV_" in _installed
                                             or "Vert" in _installed)
            else paint_hp_numeric if ("_N_" in _installed
                                       or "Numeric" in _installed)
            else paint_hp_indicator)
