"""Azeo faceplate symbol vocabulary: original, code-native, scalable.

The action buttons and state marks on a faceplate are painted here, not
loaded from artwork, so they stay crisp at any Studio zoom and carry no
third-party imagery. The set is Azeo's own: a rounded-square button shell
with a light face, and glyphs built from what each action means, coloured
with the suite's icon families (``ICON_FAMILIES`` in
``core/presentation/brand.py``) so a faceplate row matches the engineering
ribbons and trees. Recognition colours stay fixed across operator themes.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)


ICON_TITLES = {
    "module_detail": "Module detail display",
    "block_faceplate": "Associated block faceplate",
    "primary_control": "Primary control display",
    "control_designer": "Control Designer",
    "process_history": "Process History View",
    "acknowledge_alarms": "Acknowledge visible alarms",
    "alarm": "Alarm",
    "alarm_help": "Alarm help",
    "faceplate_return": "Return to faceplate",
    "simulate": "Simulation active",
    "mini_faceplate": "Mini faceplate",
}

FACEPLATE_ICONS = tuple(ICON_TITLES)

#: The faceplate surfaces name their six footer actions by short keys; every
#: row paints the same symbol for the same action through this map.
ACTION_ICON_NAMES = {
    "detail": "module_detail",
    "dcc": "block_faceplate",
    "primary": "primary_control",
    "studio": "control_designer",
    "history": "process_history",
    "ack": "acknowledge_alarms",
}

# These are drawing-library symbols, distinct from the ``icon_button``
# element used by a ready-made faceplate.  Keeping the same stable names lets
# an engineer start with the documented artwork, group it into a PVM or
# faceplate, and add an Interaction action only when there is something real
# for the Operator Station to call.
SPECIAL_SYMBOLS = FACEPLATE_ICONS

SPECIAL_SYMBOL_GROUPS = {
    "Faceplate Actions": (
        "module_detail", "block_faceplate", "primary_control",
        "control_designer", "process_history", "acknowledge_alarms",
        "faceplate_return",
    ),
    "Status and Assistance": (
        "alarm", "alarm_help", "simulate", "mini_faceplate",
    ),
}

# The six footer actions and the return-to-faceplate action sit in a 31 px
# rounded-square button shell.  State marks such as Simulate, Alarm and Help
# carry their own plate; wrapping those again would draw a double frame.
SPECIAL_SYMBOL_BUTTONS = frozenset({
    "module_detail",
    "block_faceplate",
    "primary_control",
    "control_designer",
    "process_history",
    "acknowledge_alarms",
    "faceplate_return",
})

# Native sizes of the marks on the fixed faceplate surfaces.  Palette previews
# scale them up for selection, but a click places the mark at the proportion
# a faceplate uses rather than making every state glyph 32 px.
SPECIAL_SYMBOL_SIZES = {
    **{name: (31, 31) for name in SPECIAL_SYMBOL_BUTTONS},
    "alarm": (16, 16),
    "alarm_help": (16, 16),
    "simulate": (18, 18),
    "mini_faceplate": (16, 16),
}


def _families() -> dict:
    """The suite's icon colour families (light, base, dark), read lazily so this
    Qt-free-at-import painter module never pulls the presentation package early."""
    from azeo_control_trainer.core.presentation.brand import ICON_FAMILIES
    return ICON_FAMILIES


def _family(name: str) -> tuple[QColor, QColor, QColor]:
    light, base, dark = _families()[name]
    return QColor(light), QColor(base), QColor(dark)


def _pt(rect: QRectF, x: float, y: float) -> QPointF:
    return QPointF(rect.left() + rect.width() * x, rect.top() + rect.height() * y)


def _stroke(painter: QPainter, colour, width: float, cap=Qt.RoundCap) -> None:
    painter.setPen(QPen(colour, width, Qt.SolidLine, cap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)


def _plate(painter: QPainter, shape, family: str, *, radius: float = 0.0) -> None:
    """Colour-with-depth fill: light-to-base gradient, dark rim, top highlight."""
    light, base, dark = _family(family)
    bounds = shape.boundingRect() if isinstance(shape, QPainterPath) else shape
    gradient = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
    gradient.setColorAt(0.0, light)
    gradient.setColorAt(1.0, base)
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(dark, max(0.8, bounds.height() * 0.06)))
    if isinstance(shape, QPainterPath):
        painter.drawPath(shape)
    else:
        painter.drawRoundedRect(shape, radius, radius)
    painter.setPen(QPen(QColor(255, 255, 255, 150), max(0.6, bounds.height() * 0.05)))
    painter.drawLine(_pt(bounds, .22, .16), _pt(bounds, .78, .16))


def _button_shell(painter: QPainter, rect: QRectF, pressed: bool) -> QRectF:
    """Rounded-square button with a light face; returns the glyph area."""
    inset = min(rect.width(), rect.height()) * 0.06
    box = rect.adjusted(inset, inset, -inset, -inset)
    gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
    gradient.setColorAt(0.0, QColor("#D9DDE2" if pressed else "#FBFBFC"))
    gradient.setColorAt(1.0, QColor("#C9CED4" if pressed else "#E6E9ED"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor("#9AA3AD"), max(0.8, rect.width() / 34.0)))
    painter.drawRoundedRect(box, box.width() * .26, box.height() * .26)
    return box.adjusted(box.width() * .20, box.height() * .20,
                        -box.width() * .20, -box.height() * .20)


WHITE = QColor(255, 255, 255, 235)


def draw_faceplate_icon(painter: QPainter, rect: QRectF, name: str, *,
                        button: bool = False, pressed: bool = False,
                        enabled: bool = True) -> None:
    """Paint one faceplate mark inside ``rect``.

    ``button`` is explicit.  A catalogue icon with no interaction is artwork,
    not a control; once an action is assigned the shared item painter asks for
    the button shell and the affordance becomes truthful.
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setOpacity(1.0 if enabled else 0.42)
    g = _button_shell(painter, rect, pressed) if button else rect.adjusted(
        rect.width() * .12, rect.height() * .12, -rect.width() * .12, -rect.height() * .12)
    s = min(g.width(), g.height())
    _light_doc, doc, dark_doc = _family("document")
    _light_navy, navy, dark_navy = _family("hardware")
    _light_teal, teal, dark_teal = _family("graphics")
    _light_slate, slate, _dark_slate = _family("tool")
    line = max(1.0, s * .09)

    if name == "module_detail":
        card = QRectF(g.left() + s * .04, g.top() + s * .06, s * .74, s * .88)
        _plate(painter, card, "document", radius=s * .08)
        painter.setPen(Qt.NoPen)
        painter.setBrush(WHITE)
        painter.drawRoundedRect(QRectF(card.left() + s * .10, card.top() + s * .12, s * .54, s * .12),
                                s * .04, s * .04)
        for y in (.44, .62):
            painter.drawRoundedRect(QRectF(card.left() + s * .10, card.top() + s * y, s * .44, s * .08),
                                    s * .03, s * .03)
        _stroke(painter, dark_doc, line * .9)
        painter.drawPolyline([_pt(g, .78, .62), _pt(g, .96, .80), _pt(g, .78, .98)])
    elif name == "block_faceplate":
        card = QRectF(g.left() + s * .22, g.top() + s * .02, s * .56, s * .96)
        _plate(painter, card, "hardware", radius=s * .10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(WHITE)
        painter.drawRoundedRect(QRectF(card.left() + s * .14, card.top() + s * .14, s * .12, s * .58),
                                s * .04, s * .04)
        painter.setBrush(teal)
        painter.drawRoundedRect(QRectF(card.left() + s * .14, card.top() + s * .40, s * .12, s * .32),
                                s * .04, s * .04)
        _stroke(painter, WHITE, line * .7, Qt.FlatCap)
        painter.drawLine(QPointF(card.left() + s * .32, card.top() + s * .40),
                         QPointF(card.left() + s * .44, card.top() + s * .40))
        painter.setPen(Qt.NoPen)
        painter.setBrush(WHITE)
        for x in (.30, .42):
            painter.drawEllipse(QPointF(card.left() + s * x, card.bottom() - s * .12), s * .045, s * .045)
    elif name == "primary_control":
        screen = QRectF(g.left() + s * .02, g.top() + s * .06, s * .96, s * .70)
        _plate(painter, screen, "graphics", radius=s * .10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dark_teal)
        painter.drawRoundedRect(QRectF(g.left() + s * .34, g.top() + s * .80, s * .32, s * .12),
                                s * .04, s * .04)
        painter.setBrush(WHITE)
        painter.drawRoundedRect(QRectF(screen.left() + s * .16, screen.top() + s * .16, s * .26, s * .40),
                                s * .06, s * .06)
        _stroke(painter, WHITE, line * .75, Qt.FlatCap)
        painter.drawPolyline([_pt(screen, .44, .52), _pt(screen, .62, .52),
                              _pt(screen, .62, .30), _pt(screen, .84, .30)])
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(_pt(screen, .84, .30), s * .06, s * .06)
    elif name == "control_designer":
        block = QRectF(g.left() + s * .24, g.top() + s * .16, s * .52, s * .68)
        _plate(painter, block, "hardware", radius=s * .08)
        _stroke(painter, dark_navy, line * .8, Qt.FlatCap)
        for y in (.34, .66):
            painter.drawLine(_pt(g, .02, y), _pt(g, .24, y))
        painter.drawLine(_pt(g, .76, .50), _pt(g, .98, .50))
        painter.setPen(Qt.NoPen)
        painter.setBrush(WHITE)
        painter.drawRoundedRect(QRectF(block.left() + s * .12, block.top() + s * .26, s * .28, s * .10),
                                s * .03, s * .03)
        painter.drawRoundedRect(QRectF(block.left() + s * .12, block.top() + s * .44, s * .20, s * .10),
                                s * .03, s * .03)
    elif name == "process_history":
        _stroke(painter, slate, line * .75, Qt.FlatCap)
        painter.drawPolyline([_pt(g, .08, .06), _pt(g, .08, .92), _pt(g, .96, .92)])
        painter.setPen(QPen(slate, line * .5, Qt.DashLine))
        painter.drawLine(_pt(g, .70, .10), _pt(g, .70, .88))
        path = QPainterPath(_pt(g, .12, .70))
        path.cubicTo(_pt(g, .30, .74), _pt(g, .36, .26), _pt(g, .52, .40))
        path.cubicTo(_pt(g, .62, .50), _pt(g, .74, .36), _pt(g, .92, .30))
        _stroke(painter, doc, line * 1.05)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dark_doc)
        painter.drawEllipse(_pt(g, .70, .42), s * .07, s * .07)
    elif name == "acknowledge_alarms":
        _plate(painter, QRectF(g.left() + s * .04, g.top() + s * .04, s * .92, s * .92), "alarm",
               radius=s * .22)
        _stroke(painter, QColor("white"), line * 1.15)
        painter.drawPolyline([_pt(g, .24, .52), _pt(g, .43, .72), _pt(g, .78, .30)])
    elif name == "alarm":
        _plate(painter, QRectF(g.left() + s * .04, g.top() + s * .04, s * .92, s * .92), "stop",
               radius=s * .22)
        _stroke(painter, QColor("white"), line * 1.15)
        painter.drawLine(_pt(g, .50, .24), _pt(g, .50, .58))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(_pt(g, .50, .76), s * .075, s * .075)
    elif name == "alarm_help":
        book = QPainterPath(_pt(g, .50, .22))
        book.cubicTo(_pt(g, .34, .12), _pt(g, .18, .14), _pt(g, .06, .20))
        book.lineTo(_pt(g, .06, .84))
        book.cubicTo(_pt(g, .20, .78), _pt(g, .36, .78), _pt(g, .50, .90))
        book.cubicTo(_pt(g, .64, .78), _pt(g, .80, .78), _pt(g, .94, .84))
        book.lineTo(_pt(g, .94, .20))
        book.cubicTo(_pt(g, .82, .14), _pt(g, .66, .12), _pt(g, .50, .22))
        book.closeSubpath()
        _plate(painter, book, "document")
        _stroke(painter, QColor(255, 255, 255, 220), line * .55, Qt.FlatCap)
        painter.drawLine(_pt(g, .50, .24), _pt(g, .50, .88))
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", max(6, int(s * .40)), QFont.Bold))
        painter.drawText(QRectF(g.left() + s * .52, g.top() + s * .22, s * .38, s * .56),
                         Qt.AlignCenter, "?")
    elif name == "faceplate_return":
        _stroke(painter, navy, line * 1.05)
        arrow = QPainterPath(_pt(g, .88, .30))
        arrow.lineTo(_pt(g, .40, .30))
        arrow.cubicTo(_pt(g, .12, .30), _pt(g, .12, .72), _pt(g, .40, .72))
        arrow.lineTo(_pt(g, .62, .72))
        painter.drawPath(arrow)
        painter.drawPolyline([_pt(g, .50, .56), _pt(g, .66, .72), _pt(g, .50, .88)])
    elif name == "simulate":
        painter.setPen(QPen(QColor("#9AA3AD"), max(0.8, s * .05)))
        painter.setBrush(QBrush(QColor("#E6E9ED")))
        painter.drawEllipse(g)
        flask = QPainterPath(_pt(g, .40, .22))
        flask.lineTo(_pt(g, .40, .48))
        flask.lineTo(_pt(g, .24, .78))
        flask.lineTo(_pt(g, .76, .78))
        flask.lineTo(_pt(g, .60, .48))
        flask.lineTo(_pt(g, .60, .22))
        flask.closeSubpath()
        _plate(painter, flask, "graphics")
        _stroke(painter, dark_teal, line * .5, Qt.FlatCap)
        painter.drawLine(_pt(g, .34, .22), _pt(g, .66, .22))
    elif name == "mini_faceplate":
        _stroke(painter, QColor("#70767D"), max(1.0, s * .09))
        painter.drawPolyline([_pt(g, .25, .62), _pt(g, .50, .36), _pt(g, .75, .62)])
    else:
        _stroke(painter, QColor("#4D535B"), max(1.0, s * .06))
        painter.drawRect(g)
    painter.restore()


def special_symbol_pixmap(name: str, width: int = 48,
                          height: int = 48) -> QPixmap:
    """Return the exact scalable Special Symbol preview used on the canvas."""
    pixmap = QPixmap(max(1, int(width)), max(1, int(height)))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    draw_faceplate_icon(
        painter, QRectF(0, 0, pixmap.width(), pixmap.height()), name,
        button=name in SPECIAL_SYMBOL_BUTTONS,
    )
    painter.end()
    return pixmap


__all__ = [
    "ACTION_ICON_NAMES",
    "FACEPLATE_ICONS",
    "ICON_TITLES",
    "SPECIAL_SYMBOLS",
    "SPECIAL_SYMBOL_BUTTONS",
    "SPECIAL_SYMBOL_GROUPS",
    "SPECIAL_SYMBOL_SIZES",
    "draw_faceplate_icon",
    "special_symbol_pixmap",
]
