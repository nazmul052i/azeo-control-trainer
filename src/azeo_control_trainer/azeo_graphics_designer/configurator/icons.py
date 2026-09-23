"""Configuration uses the same blue vector vocabulary as Graphics Designer."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen

from azeo_control_trainer.azeo_graphics_designer.component_icons import _dpr, _pixmap, element_icon
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from azeo_control_trainer.core.presentation.studio_icons import draw_icon


def command_icon(name: str, size: int = 22):
    from azeo_control_trainer.core.presentation.icon_set import has_icon
    shared = {"validate": "verify", "block": "lib_block"}.get(name, name)
    if has_icon(shared):
        return draw_icon(shared, size, WF["lapis"])
    if name in ("validate", "help", "designer", "image", "block", "add_group", "add_property"):
        pixmap = _pixmap(size, size, _dpr())
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(size / 24, size / 24)
            painter.setPen(QPen(QColor(WF["lapis"]), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

            def line(a, b, c, d):
                painter.drawLine(QPointF(a, b), QPointF(c, d))

            if name in ("add_group", "add_property"):
                base = draw_icon("open" if name == "add_group" else "new", 24, WF["lapis"])
                base.paint(painter, 0, 0, 22, 22)
                painter.setBrush(QColor("white"))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QRectF(13, 12, 11, 11))
                painter.setPen(QPen(QColor(WF["lapis"]), 1.7, Qt.SolidLine, Qt.RoundCap))
                line(18, 14, 18, 22)
                line(14, 18, 22, 18)
            elif name == "help":
                painter.drawEllipse(QRectF(3, 3, 18, 18))
                path = QPainterPath(QPointF(9, 9))
                path.cubicTo(9, 5, 17, 6, 14, 11)
                path.lineTo(12, 13)
                painter.drawPath(path)
                painter.drawPoint(QPointF(12, 17))
            else:
                painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
                if name == "validate":
                    line(7, 12, 11, 16)
                    line(11, 16, 18, 8)
                elif name == "designer":
                    line(3, 8, 21, 8)
                    line(9, 8, 9, 20)
                    line(16, 8, 16, 20)
                    line(11, 11, 14, 11)
                    line(11, 15, 14, 15)
                elif name == "block":
                    line(0, 9, 5, 9)
                    line(0, 15, 5, 15)
                    line(19, 9, 24, 9)
                    line(19, 15, 24, 15)
                    line(9, 9, 15, 9)
                    line(9, 12, 13, 12)
                    line(9, 15, 15, 15)
                else:
                    painter.drawEllipse(QRectF(6, 7, 3, 3))
                    line(4, 18, 11, 12)
                    line(11, 12, 16, 17)
                    line(16, 17, 20, 12)
        finally:
            painter.end()
        return QIcon(pixmap)
    return draw_icon(name, size, WF["lapis"])


def property_icon(ptype: str, size: int = 20):
    elements = {
        "Boolean": "check_box", "String": "text_entry", "Number": "datalink",
        "Color": "style_brush", "Font": "text", "Measurement": "line",
        "Degree Angle": "arc", "Selection": "combo_box",
    }
    if ptype in elements:
        return element_icon(elements[ptype], size)
    return command_icon({
        "Image": "image", "Multi-language String": "comment",
        "Control Tag": "xref", "Function Block Reference": "block",
        "Parameter Reference": "params",
    }.get(ptype, "properties"), size)
