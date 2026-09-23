"""Studio stencils and command marks, rendered from vector geometry at screen DPI.

These are authoring aids. The real PVM painter supplies class thumbnails;
function-block glyphs identify similar-looking compact and inline variants.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from azeo_control_trainer.core.presentation.function_block_icons import draw_block_icon


# Reuse the engineering glyph vocabulary, including explicit equivalents for
# display-only families. No installed class should fall through to the dot.
FB_ICON_TYPES = {
    "AI": "AI", "AO": "AO", "DI": "DI", "DO": "DO", "PID": "PID",
    "ALARM": "ALARM", "ALARM_DET": "ALARM", "AT": "AT",
    "AREA": "COMPOSITE", "AVTR": "SIS_VOTER", "DVTR": "SIS_VOTER",
    "CEM": "CEM", "CTLSL": "MIN_SELECT", "DEVCTL": "DEVCTL",
    "DMC_CONTROLLER": "DMC_CONTROLLER", "EDC": "DEVCTL", "FLC": "PID",
    "GAIN_SCHED": "GAIN_SCHED", "INSPECT": "INPUT_PARAMETER",
    "ISEL": "MIN_SELECT", "LE": "EXPRESSION", "MANLD": "MANLD",
    "MAX_SELECT": "MAX_SELECT", "MIN_SELECT": "MIN_SELECT",
    "MOTOR_INTERLOCK": "INTERLOCK", "ONOFF": "ONOFF", "PIN": "INPORT",
    "RAMP_SOAK": "RAMP_SOAK", "RATIO": "RATIO", "SEQ": "SEQ",
    "SFC_CHART": "SFC_CHART", "STD": "STD", "TOTALIZER": "INTEGRATOR",
}


def _dpr() -> float:
    app = QApplication.instance()
    return max(1.0, app.devicePixelRatio()) if app else 1.0


def _pixmap(width: int, height: int, dpr: float) -> QPixmap:
    result = QPixmap(round(width * dpr), round(height * dpr))
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.transparent)
    return result


def block_icon(block_type: str, size: int = 20) -> QIcon:
    return QIcon(_block_pixmap(str(block_type), size, _dpr()))


@lru_cache(maxsize=256)
def _block_pixmap(block_type: str, size: int, dpr: float) -> QPixmap:
    from azeo_control_trainer.core.strategy.model.block_base import BlockCategory

    pixmap = _pixmap(size, size, dpr)
    painter = QPainter(pixmap)
    try:
        draw_block_icon(painter, QRectF(1, 1, size - 2, size - 2),
                        FB_ICON_TYPES.get(block_type, block_type),
                        BlockCategory.CONTROL, QColor(WF["lapis"]))
    finally:
        painter.end()
    return pixmap


def pvm_icon(cls, size: int = 20) -> QIcon:
    """A stable block identity in all class trees, including faceplates."""
    return block_icon(cls.block_type, size)


def pvm_preview(cls, width: int, height: int) -> QPixmap:
    from .configurator.designer import _coded_class_preview

    # Configurator's large preview has generous margins. Thumbnail mode fits
    # the complete bounding rect, including the HP tag above the body.
    return _coded_class_preview(cls, width, height, thumbnail=True, dpr=_dpr())


def class_stencil_preview(cls, width: int, height: int) -> QPixmap:
    variant = getattr(cls, "variant", "")
    if not variant or (not variant.startswith("hp") and cls.block_type in (
            "AVTR", "DVTR", "CEM", "DMC_CONTROLLER")):
        # Compact FBs share the same value-card silhouette when unbound.
        # Their engineering glyph carries the useful identity at palette size.
        result = _pixmap(width, height, _dpr())
        painter = QPainter(result)
        try:
            icon = _block_pixmap(cls.block_type, 40, _dpr())
            painter.drawPixmap((width - 40) // 2, (height - 40) // 2, icon)
        finally:
            painter.end()
        return result
    return pvm_preview(cls, width, height)


def authored_preview(library, name, config, width: int, height: int,
                     *, dpr: float | None = None, fit: bool = True, choices=None) -> QPixmap:
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import route_scene_pipes
    from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource

    records = library.instantiate(name, 0.0, 0.0, config=config, choices=choices or {})
    if not records:
        return element_preview("component", width, height)
    renderer = DisplayRenderer(BindingEngine(LiveGraphSource(lambda: {})), THEMES[DEFAULT_THEME])
    scene = QGraphicsScene()
    # Retain wrappers until rendering and native-scene teardown complete.
    items = [renderer.build_drawing(record) for record in records]
    for item in items:
        scene.addItem(item)
    route_scene_pipes(scene)
    result = _pixmap(width, height, _dpr() if dpr is None else dpr)
    painter = QPainter(result)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = scene.itemsBoundingRect()
        scale = min((width - 6) / max(1, bounds.width()),
                    (height - 6) / max(1, bounds.height()))
        if not fit:
            scale = min(scale, 1.0)
        target = QRectF((width - bounds.width() * scale) / 2,
                        (height - bounds.height() * scale) / 2,
                        bounds.width() * scale, bounds.height() * scale)
        scene.render(painter, target, bounds, Qt.KeepAspectRatio)
    finally:
        painter.end()
        scene.clear()
    return result


def symbol_preview(name: str, width: int, height: int) -> QPixmap:
    from azeo_control_trainer.core.hmi.pvms.symbols import preview_pixmap

    dpr = _dpr()
    pixmap = preview_pixmap(name, round(width * dpr), round(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def special_preview(name: str, width: int, height: int) -> QPixmap:
    from azeo_control_trainer.core.hmi.pvms.faceplate_icons import special_symbol_pixmap

    dpr = _dpr()
    pixmap = special_symbol_pixmap(name, round(width * dpr), round(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def element_icon(kind: str, size: int = 22) -> QIcon:
    return QIcon(element_preview(kind, size, size))


def element_preview(kind: str, width: int, height: int) -> QPixmap:
    return _element_preview(kind, width, height, _dpr())


@lru_cache(maxsize=256)
def _element_preview(kind: str, width: int, height: int, dpr: float) -> QPixmap:
    """Recognizable control silhouettes; no unlabeled placeholder bars."""
    from azeo_control_trainer.core.hmi.pvms.shapes import SHAPE_KINDS, shape_path

    result = _pixmap(width, height, dpr)
    p = QPainter(result)
    try:
        p.setRenderHint(QPainter.Antialiasing)
        scale = min(width / 40.0, height / 32.0)
        p.translate((width - 40 * scale) / 2, (height - 32 * scale) / 2)
        p.scale(scale, scale)
        pen = QPen(QColor(WF["lapis"]), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QColor(WF["sel"]))

        def line(x1, y1, x2, y2):
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        def text(value, rect=QRectF(4, 6, 32, 20)):
            font = QFont("Segoe UI")
            font.setPixelSize(12)
            font.setBold(True)
            p.setFont(font)
            p.drawText(rect, Qt.AlignCenter, value)

        def path(points):
            one = QPainterPath(QPointF(*points[0]))
            for point in points[1:]:
                one.lineTo(*point)
            p.drawPath(one)

        box = QRectF(5, 7, 30, 18)
        if kind in SHAPE_KINDS:
            p.drawPath(shape_path(kind, box, {}))
        elif kind in ("rect", "square", "round_rect", "button", "text_entry", "combo_box"):
            p.drawRoundedRect(box, 3 if kind in ("round_rect", "button") else 1, 3)
            if kind == "button":
                text("OK")
            elif kind == "text_entry":
                text("Ab", QRectF(7, 8, 21, 16))
                line(28, 10, 28, 22)
            elif kind == "combo_box":
                line(25, 8, 25, 24)
                path([(28, 14), (31, 18), (34, 14)])
                line(9, 16, 20, 16)
        elif kind in ("ellipse", "radio_button"):
            p.drawEllipse(box if kind == "ellipse" else QRectF(11, 7, 18, 18))
            if kind == "radio_button":
                p.setBrush(QColor(WF["lapis"]))
                p.drawEllipse(QRectF(16, 12, 8, 8))
        elif kind == "check_box":
            p.drawRoundedRect(QRectF(10, 6, 20, 20), 3, 3)
            path([(14, 16), (18, 20), (26, 11)])
        elif kind == "slider":
            line(5, 16, 35, 16)
            p.drawRoundedRect(QRectF(22, 9, 5, 14), 2, 2)
        elif kind == "slew":
            p.drawRoundedRect(box, 2, 2)
            line(25, 7, 25, 25)
            path([(28, 13), (31, 10), (34, 13)])
            path([(28, 19), (31, 22), (34, 19)])
            text("42", QRectF(6, 8, 18, 16))
        elif kind in ("text", "datalink"):
            text("Aa" if kind == "text" else "12.3")
            line(7, 25, 33, 25)
        elif kind == "display_link":
            p.drawRoundedRect(box, 2, 2)
            path([(17, 16), (31, 16), (26, 11)])
            line(31, 16, 26, 21)
        elif kind in ("chart", "multi_point", "radar_plot"):
            p.setBrush(Qt.NoBrush)
            if kind == "radar_plot":
                path([(20, 4), (35, 13), (30, 27), (10, 27), (5, 13), (20, 4)])
                path([(20, 10), (28, 15), (26, 22), (13, 23), (12, 14), (20, 10)])
            else:
                path([(5, 6), (5, 26), (35, 26)])
                path([(8, 21), (14, 14), (20, 18), (27, 8), (34, 11)])
        elif kind in ("table", "alarm_list", "tab"):
            p.drawRoundedRect(QRectF(4, 5, 32, 22), 2, 2)
            for y in (12, 19):
                line(4, y, 36, y)
            if kind == "table":
                line(15, 5, 15, 27)
                line(26, 5, 26, 27)
            elif kind == "alarm_list":
                text("!", QRectF(5, 11, 9, 15))
                line(17, 15, 31, 15)
                line(17, 23, 31, 23)
            else:
                line(15, 5, 15, 12)
                line(26, 5, 26, 12)
        elif kind == "date_time":
            p.drawEllipse(QRectF(9, 5, 22, 22))
            path([(20, 9), (20, 16), (26, 19)])
        elif kind in {
                "surface", "title", "value", "pv_bar", "setpoint",
                "out_bar", "trend", "alarms", "unit", "conditions",
                "state_list"}:
            p.setBrush(QColor(WF["page"]))
            p.drawRoundedRect(QRectF(6, 3, 28, 26), 2, 2)
            p.setBrush(QColor(WF["sel"]))
            if kind == "surface":
                p.drawRect(QRectF(10, 7, 20, 18))
            elif kind == "title":
                line(10, 9, 30, 9)
                line(13, 15, 27, 15)
                line(16, 21, 24, 21)
            elif kind == "value":
                text("PV", QRectF(8, 7, 11, 16))
                text("42", QRectF(19, 7, 13, 16))
            elif kind == "pv_bar":
                p.drawRect(QRectF(18, 6, 6, 20))
                p.setBrush(QColor(WF["lapis"]))
                p.drawRect(QRectF(20, 14, 2, 10))
                line(12, 7, 16, 7)
                line(12, 25, 16, 25)
            elif kind == "setpoint":
                text("SP", QRectF(8, 8, 13, 16))
                path([(25, 13), (29, 9), (33, 13)])
                path([(25, 19), (29, 23), (33, 19)])
            elif kind == "out_bar":
                p.drawRect(QRectF(9, 14, 22, 6))
                p.setBrush(QColor(WF["lapis"]))
                p.drawRect(QRectF(10, 16, 13, 2))
            elif kind == "trend":
                path([(9, 23), (14, 15), (19, 19), (25, 8), (31, 12)])
                line(9, 25, 31, 25)
            elif kind == "alarms":
                text("!", QRectF(8, 7, 8, 17))
                line(18, 10, 30, 10)
                line(18, 16, 30, 16)
                line(18, 22, 27, 22)
            elif kind == "unit":
                text("U", QRectF(12, 6, 16, 20))
            elif kind == "conditions":
                for y, checked in ((9, True), (16, False), (23, True)):
                    p.drawRect(QRectF(10, y - 3, 5, 5))
                    if checked:
                        path([(11, y - 1), (13, y + 1), (16, y - 3)])
                    line(19, y - 1, 30, y - 1)
            else:  # state_list
                for number, y in ((1, 9), (2, 16), (3, 23)):
                    text(str(number), QRectF(9, y - 5, 7, 8))
                    line(18, y - 1, 30, y - 1)
        elif kind in ("pencil", "style_brush"):
            if kind == "pencil":
                path([(10, 22), (26, 6), (31, 11), (15, 27), (8, 29), (10, 22)])
                line(23, 9, 28, 14)
            else:
                p.drawRoundedRect(QRectF(7, 5, 24, 9), 2, 2)
                path([(31, 9), (35, 9), (35, 18), (20, 18), (20, 22)])
                p.drawRoundedRect(QRectF(17, 22, 6, 8), 2, 2)
        elif kind in ("pipe", "polyline", "line", "freehand", "arc"):
            p.setBrush(Qt.NoBrush)
            if kind == "pipe":
                path([(5, 23), (15, 23), (15, 9), (35, 9)])
                p.drawEllipse(QRectF(3, 21, 4, 4))
                p.drawEllipse(QRectF(33, 7, 4, 4))
            elif kind == "arc":
                p.drawArc(box, 0, 180 * 16)
            elif kind in ("freehand", "pencil"):
                one = QPainterPath(QPointF(5, 24))
                one.cubicTo(10, 0, 25, 32, 35, 6)
                p.drawPath(one)
            else:
                path([(5, 24), (17, 10), (27, 22), (35, 7)]
                     if kind == "polyline" else [(5, 24), (35, 7)])
        elif kind == "group":
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(5, 5, 18, 17), 2, 2)
            p.drawRoundedRect(QRectF(17, 13, 18, 17), 2, 2)
        elif kind == "select":
            path([(10, 4), (10, 26), (17, 20), (22, 28), (27, 25), (22, 18), (31, 17), (10, 4)])
        elif kind == "pan":
            one = QPainterPath(QPointF(12, 16))
            one.lineTo(12, 7)
            one.cubicTo(12, 4, 16, 4, 16, 7)
            one.lineTo(16, 14)
            one.lineTo(16, 4)
            one.cubicTo(16, 1, 20, 1, 20, 4)
            one.lineTo(20, 14)
            one.lineTo(20, 6)
            one.cubicTo(20, 3, 24, 3, 24, 6)
            one.lineTo(24, 15)
            one.lineTo(24, 9)
            one.cubicTo(24, 6, 28, 6, 28, 9)
            one.lineTo(28, 21)
            one.cubicTo(28, 30, 17, 31, 12, 24)
            one.lineTo(7, 18)
            one.cubicTo(5, 14, 9, 12, 12, 16)
            p.drawPath(one)
        elif kind in ("incoming", "outgoing"):
            p.save()
            if kind == "incoming":
                p.translate(40, 0)
                p.scale(-1, 1)
            path([(5, 9), (25, 9), (35, 16), (25, 23), (5, 23), (5, 9)])
            p.restore()
        else:
            # Authored classes with no previewable geometry retain a named
            # component glyph; they never impersonate a process reading.
            path([(20, 4), (35, 12), (20, 20), (5, 12), (20, 4)])
            path([(5, 18), (20, 27), (35, 18)])
    finally:
        p.end()
    return result


class ComponentPreview(QLabel):
    """Lazy preview avoids painting hundreds of hidden library classes at boot."""

    def __init__(self, factory, parent=None):
        super().__init__(parent)
        self.setObjectName("component_preview")
        self._factory = factory
        self._loaded = False
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(64)
        self.setStyleSheet("border: none; background: transparent;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def ensure_loaded(self):
        if not self._loaded:
            try:
                self.setPixmap(self._factory())
            except Exception as error:
                import logging
                from azeo_control_trainer.core.presentation.studio_icons import studio_icon

                logging.getLogger("azeo.graphics_designer.icons").exception("Component preview failed")
                self.setProperty("previewError", str(error))
                self.setToolTip(f"Preview unavailable: {error}")
                self.setPixmap(studio_icon("diagnostics", 32).pixmap(32, 32))
            self._loaded = True

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.ensure_loaded()


class PaletteScrollArea(QScrollArea):
    """Keep search reachable while the engineer browses a long stencil list."""

    def set_header(self, header):
        self.header = header
        header.setParent(self)
        self.update_header_geometry()
        header.show()

    def update_header_geometry(self):
        if not hasattr(self, "header"):
            return
        height = self.header.sizeHint().height()
        self.setViewportMargins(0, height, 0, 0)
        self.header.setGeometry(0, 0, self.width(), height)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.update_header_geometry()
