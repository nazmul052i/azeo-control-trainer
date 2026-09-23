"""Code-native application icon family for the Azeo product suite.

Every product keeps the same blue enclosure and optical proportions while its
white/accent mark describes the job it performs. Drawing the marks instead of
shipping one bitmap keeps 16 px title-bar icons and 256 px Windows resources
equally crisp.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)


APP_ID_PREFIX = "Azeo.ControlTrainer"
APPLICATION_ICONS = {
    "explorer": ("#55C2E8", "System hierarchy"),
    "control_designer": ("#55C9B4", "Function-block engineering"),
    "graphics_designer": ("#B596F2", "Graphics authoring"),
    "operator_station": ("#77BDF2", "Operator console"),
    "simulation_workbench": ("#F0A65A", "Simulation workbench"),
    "pa_designer": ("#C8A7F2", "Procedure workflow"),
    "simulator": ("#54C7BE", "Plant simulator"),
    "help": ("#9FC9F4", "Offline help"),
    "suite": ("#70B9E8", "Azeo suite"),
}


def _key(value) -> str:
    raw = getattr(value, "value", value) or "explorer"
    key = str(raw).strip().casefold().replace("-", "_")
    aliases = {
        "control": "control_designer",
        "graphics": "graphics_designer",
        "station": "operator_station",
        "simulation": "simulation_workbench",
        "procedures": "pa_designer",
    }
    key = aliases.get(key, key)
    return key if key in APPLICATION_ICONS else "suite"


def _stroke(painter: QPainter, colour, width=5.5):
    pen = QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _paint_explorer(painter, accent):
    _stroke(painter, "#FFFFFF", 5.5)
    painter.drawLine(QPointF(50, 31), QPointF(50, 48))
    painter.drawLine(QPointF(28, 48), QPointF(72, 48))
    painter.drawLine(QPointF(28, 48), QPointF(28, 64))
    painter.drawLine(QPointF(72, 48), QPointF(72, 64))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(accent))
    for rect in (
        QRectF(41, 20, 18, 16),
        QRectF(19, 63, 18, 16),
        QRectF(63, 63, 18, 16),
    ):
        painter.drawRoundedRect(rect, 3, 3)


def _paint_control(painter, accent):
    _stroke(painter, "#FFFFFF", 5)
    painter.drawRoundedRect(QRectF(17, 25, 26, 24), 4, 4)
    painter.drawRoundedRect(QRectF(57, 52, 26, 24), 4, 4)
    painter.drawLine(QPointF(43, 37), QPointF(53, 37))
    painter.drawLine(QPointF(53, 37), QPointF(53, 64))
    painter.drawLine(QPointF(53, 64), QPointF(57, 64))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(accent))
    for point in (
        QPointF(17, 37),
        QPointF(43, 37),
        QPointF(57, 64),
        QPointF(83, 64),
    ):
        painter.drawEllipse(point, 4.2, 4.2)


def _paint_graphics(painter, accent):
    _stroke(painter, "#FFFFFF", 4.5)
    path = QPainterPath(QPointF(22, 65))
    path.cubicTo(QPointF(32, 27), QPointF(66, 26), QPointF(76, 57))
    painter.drawPath(path)
    painter.setBrush(QColor(accent))
    painter.setPen(QPen(QColor("#FFFFFF"), 3.5))
    for point in (QPointF(22, 65), QPointF(42, 34), QPointF(76, 57)):
        painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(QPolygonF((
        QPointF(57, 75), QPointF(77, 55),
        QPointF(83, 61), QPointF(63, 81),
    )))


def _paint_operator(painter, accent):
    _stroke(painter, "#FFFFFF", 5)
    painter.drawRoundedRect(QRectF(16, 22, 68, 49), 5, 5)
    painter.drawLine(QPointF(40, 80), QPointF(60, 80))
    painter.drawLine(QPointF(50, 71), QPointF(50, 80))
    trend = QPainterPath(QPointF(24, 56))
    trend.lineTo(QPointF(36, 48))
    trend.lineTo(QPointF(47, 53))
    trend.lineTo(QPointF(60, 35))
    trend.lineTo(QPointF(76, 39))
    painter.setPen(QPen(
        QColor(accent), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(trend)


def _paint_workbench(painter, accent):
    _stroke(painter, "#FFFFFF", 5)
    painter.drawLine(QPointF(37, 20), QPointF(63, 20))
    painter.drawLine(QPointF(42, 20), QPointF(42, 43))
    painter.drawLine(QPointF(58, 20), QPointF(58, 43))
    vessel = QPainterPath(QPointF(42, 43))
    vessel.lineTo(QPointF(25, 73))
    vessel.quadTo(QPointF(23, 80), QPointF(33, 80))
    vessel.lineTo(QPointF(67, 80))
    vessel.quadTo(QPointF(77, 80), QPointF(75, 73))
    vessel.lineTo(QPointF(58, 43))
    painter.drawPath(vessel)
    painter.setPen(QPen(
        QColor(accent), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    wave = QPainterPath(QPointF(32, 65))
    wave.cubicTo(QPointF(39, 58), QPointF(45, 71), QPointF(52, 64))
    wave.cubicTo(QPointF(58, 58), QPointF(63, 65), QPointF(68, 63))
    painter.drawPath(wave)


def _paint_procedure(painter, accent):
    _stroke(painter, "#FFFFFF", 5)
    painter.drawLine(QPointF(31, 29), QPointF(31, 72))
    painter.drawLine(QPointF(31, 36), QPointF(45, 36))
    painter.drawLine(QPointF(31, 64), QPointF(45, 64))
    painter.drawRoundedRect(QRectF(45, 23, 34, 25), 4, 4)
    painter.drawRoundedRect(QRectF(45, 52, 34, 25), 4, 4)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(accent))
    painter.drawEllipse(QPointF(31, 29), 6, 6)
    painter.drawEllipse(QPointF(31, 72), 6, 6)


def _paint_simulator(painter, accent):
    _stroke(painter, "#FFFFFF", 5)
    painter.drawEllipse(QRectF(25, 18, 50, 18))
    painter.drawLine(QPointF(25, 27), QPointF(25, 72))
    painter.drawLine(QPointF(75, 27), QPointF(75, 72))
    painter.drawArc(QRectF(25, 63, 50, 18), 180 * 16, 180 * 16)
    painter.setPen(QPen(
        QColor(accent), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    wave = QPainterPath(QPointF(29, 56))
    wave.cubicTo(QPointF(38, 48), QPointF(46, 63), QPointF(55, 55))
    wave.cubicTo(QPointF(62, 49), QPointF(68, 55), QPointF(71, 53))
    painter.drawPath(wave)


def _paint_help(painter, accent):
    _stroke(painter, "#FFFFFF", 4.5)
    painter.drawRoundedRect(QRectF(18, 21, 64, 58), 5, 5)
    painter.drawLine(QPointF(50, 24), QPointF(50, 76))
    painter.setPen(QPen(
        QColor(accent), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    question = QPainterPath(QPointF(59, 39))
    question.cubicTo(QPointF(59, 28), QPointF(75, 29), QPointF(75, 40))
    question.cubicTo(QPointF(75, 47), QPointF(66, 47), QPointF(66, 55))
    painter.drawPath(question)
    painter.drawPoint(QPointF(66, 65))


def _paint_suite(painter, accent):
    painter.setPen(Qt.NoPen)
    rectangles = (
        QRectF(20, 20, 25, 25), QRectF(55, 20, 25, 25),
        QRectF(20, 55, 25, 25), QRectF(55, 55, 25, 25),
    )
    for colour, rect in zip(("#FFFFFF", accent, accent, "#FFFFFF"), rectangles):
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(rect, 5, 5)


_PAINTERS = {
    "explorer": _paint_explorer,
    "control_designer": _paint_control,
    "graphics_designer": _paint_graphics,
    "operator_station": _paint_operator,
    "simulation_workbench": _paint_workbench,
    "pa_designer": _paint_procedure,
    "simulator": _paint_simulator,
    "help": _paint_help,
    "suite": _paint_suite,
}


@lru_cache(maxsize=None)
def _render_icon(application_id: str, size: int) -> QPixmap:
    key = _key(application_id)
    accent = APPLICATION_ICONS[key][0]
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(size / 100.0, size / 100.0)

    background = QLinearGradient(0, 5, 0, 95)
    background.setColorAt(0.0, QColor("#0B5A9C"))
    background.setColorAt(1.0, QColor("#06325E"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(background)
    painter.drawRoundedRect(QRectF(5, 5, 90, 90), 18, 18)
    glow = QRadialGradient(36, 28, 65)
    glow.setColorAt(0.0, QColor(120, 195, 255, 72))
    glow.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setBrush(glow)
    painter.drawRoundedRect(QRectF(7, 7, 86, 86), 16, 16)

    _PAINTERS[key](painter, accent)
    painter.end()
    return pixmap


def get_app_icon(application_id="explorer") -> QIcon:
    """Return a multi-resolution icon for one application identity."""
    key = _key(application_id)
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_render_icon(key, size))
    return icon


def apply_branding(app, application_id="explorer") -> bool:
    """Set the product icon and a distinct Windows taskbar identity."""
    key = _key(application_id)
    app.setWindowIcon(get_app_icon(key))
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{APP_ID_PREFIX}.{key}")
        return True
    except (AttributeError, OSError, ImportError):
        return False


__all__ = ["APPLICATION_ICONS", "APP_ID_PREFIX", "apply_branding", "get_app_icon"]
