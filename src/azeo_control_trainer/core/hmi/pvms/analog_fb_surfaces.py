"""Measured analog-output and pulse-input faceplate surfaces.

The two reference figures look related to ``AI_fp`` but they are not Qt
layout variants of it.  ``TAGAO_fp`` retains the narrow module shell and
adds a writable SP marker; ``PI_fp`` is a wider function-block surface whose
limits, simulation and tuning values sit beside the PV scale.  Painting both
in their documented coordinate systems keeps their anatomy stable at 96 DPI
and avoids another collection of size-hint-driven rows.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from azeo_control_trainer.core.strategy.model.terminal import Quality
from ..theme.roles import Role
from .analog_surface import AnalogFaceplateSurface
from .faceplate_icons import draw_faceplate_icon
from .loop_surface import LoopFaceplateSurface, _result, _value


class AnalogOutputFaceplateSurface(AnalogFaceplateSurface):
    """TAGAO_fp in the reference's 204 x 468 module shell."""

    WHOLE_SURFACE = True
    ACTIONS = ("detail", "dcc", "primary", "studio", "history", "ack")

    def set_identity(self, path: str,
                     description: str = "Analog Output") -> None:
        module, separator, block = str(path).partition("/")
        self.set_labels(
            eyebrow=str(path) if separator else "",
            title=module or description,
            subtitle=description,
            unit_name=module or block,
        )

    def refresh(self, bound) -> None:
        # AnalogFaceplateSurface consumes the same pv/sp/mode vocabulary.  AO
        # deliberately uses OUT as PV because that is the commanded analogue
        # value whose field readback is drawn as the secondary marker.
        super().refresh(bound)
        readback = _result(bound, "readback.value")
        if readback is not None and isinstance(readback.value, (int, float)):
            self.pv.sp_value = float(readback.value)
        self.sp_value = _value(bound, "sp.value")
        if isinstance(self.sp_value, (int, float)):
            self.state.sp = float(self.sp_value)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        scale, ox, oy = self._transform()
        painter.fillRect(self.rect(), self.BG)
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        background = QLinearGradient(0, 0, self.DESIGN_WIDTH, 0)
        background.setColorAt(0.0, self.BG)
        background.setColorAt(0.52, self.BG_ALT)
        background.setColorAt(1.0, self.BG)
        painter.fillRect(QRectF(0, 0, 204, 468), QBrush(background))

        # TAGAO has no simulation or mini marks and no mode row.  Keeping the
        # header separate from AI prevents those absent controls leaving blank
        # gutters in the only place an operator expects the PV to be.
        self._text(painter, QRectF(2, 1, 200, 15),
                   self.state.eyebrow, 9, self.TEXT)
        self._text(painter, QRectF(1, 16, 202, 29),
                   self.state.title, 17, self.TEXT)
        self._text(painter, QRectF(3, 43, 198, 18),
                   self.state.subtitle, 10, self.TEXT)
        value = "#######" if self.pv.bad else self._value(self.state.pv)
        self._text(painter, QRectF(28, 63, 106, 23), value,
                   19, self.VALUE, bold=True)
        self._text(painter, QRectF(28, 83, 106, 18), self.pv_units,
                   12, self.TEXT)
        self._analog_vertical_bar(painter)
        self._sp_marker(painter)

        painter.save()
        painter.translate(0, -63)
        super()._trend_chart(painter)
        painter.restore()
        painter.save()
        painter.translate(0, -60)
        super()._alarm_table(painter)
        painter.restore()
        self._analog_footer(painter)
        self._hover_overlay(painter)
        painter.end()

    def _sp_marker(self, painter: QPainter) -> None:
        if not isinstance(self.sp_value, (int, float)):
            return
        _scale, channel = self.pv_geometry()
        fraction = self._norm(
            float(self.sp_value), self.state.pv_min, self.state.pv_max)
        y = channel.bottom() - channel.height() * fraction
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._furniture(Role.SURFACE_FIELD, 244, 244, 244))
        painter.drawPolygon(QPolygonF([
            QPointF(72, y), QPointF(84, y - 7), QPointF(84, y + 7),
        ]))
        painter.setPen(QPen(self._furniture(Role.TEXT, 85, 85, 85), 1))
        painter.drawLine(QPointF(72, y), QPointF(84, y - 7))
        painter.drawLine(QPointF(72, y), QPointF(84, y + 7))

    @staticmethod
    def _region(name: str) -> QRectF | None:
        if name == "sp_slider":
            return QRectF(70, 104, 56, 215)
        # TAGAO has no mini-faceplate control.  Its alarm table is shifted up
        # 60 pixels from AI_fp, so its scroll hotspots must move with the ink;
        # retaining AI's old regions created an invisible button at y=91 and
        # arrows that only worked when clicked below what the operator saw.
        if name == "mini":
            return None
        if name == "alarm_up":
            return QRectF(187, 327, 14, 14)
        if name == "alarm_down":
            return QRectF(187, 344, 14, 14)
        return AnalogFaceplateSurface._region(name)

    def _hit(self, point: QPointF) -> str | None:
        if self._region("sp_slider").contains(point):
            return "sp_slider"
        return super()._hit(point)

    def _set_sp_from_y(self, y: float) -> None:
        channel = self.pv_geometry()[1]
        fraction = 1.0 - self._clamp(
            (y - channel.top()) / channel.height(), 0.0, 1.0)
        value = self.state.pv_min + fraction * (
            self.state.pv_max - self.state.pv_min)
        self.set_setpoint(value, True)


class PulseInputFaceplateSurface(LoopFaceplateSurface):
    """PI_fp painted at the measured 308 x 385 function-block size."""

    WHOLE_SURFACE = True
    DESIGN_WIDTH = 308.0
    DESIGN_HEIGHT = 385.0
    FULL_HEIGHT = 385
    MINI_HEIGHT = 385
    ACTIONS = ("faceplate",)

    action_requested = Signal(str)

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(palette, parent)
        self.setFixedSize(308, 385)
        self.field_value = None
        self.pulse_value = None
        self.time_units = None
        self.filter_value = None
        self.alarm_hysteresis = None
        self.simulate = False
        self._trend = deque(maxlen=72)
        self._trend.extend([4, 5, 5, 7, 9, 8, 7, 7, 6, 6, 5, 5])

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(308, 385)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_identity(self, path: str,
                     description: str = "Pulse Input") -> None:
        module, separator, block = str(path).partition("/")
        self.set_labels(
            eyebrow=str(path) if separator else "",
            title=module or description,
            subtitle=description,
            unit_name=module or block,
        )

    def set_available_actions(self, keys) -> None:
        self._available_actions = set(keys or ()).intersection(self.ACTIONS)
        self.update()

    def refresh(self, bound) -> None:
        pv = _result(bound, "pv.value")
        if pv is not None:
            self.pv.value = pv.value
            self.pv.bad = pv.quality is not Quality.GOOD
            self.pv.eu_range = tuple(pv.eu_range or (0.0, 100.0))
            self.pv_units = str(pv.units or _value(bound, "pv.units") or "")
            self.state.pv_min, self.state.pv_max = self.pv.eu_range
            if isinstance(pv.value, (int, float)):
                self.state.pv = float(pv.value)
                if not self.pv.bad:
                    self._trend.append(float(pv.value))
        self.pv.ticks = {
            name: float(value)
            for name, key in (("HH", "limits.hi_hi"),
                              ("H", "limits.hi"),
                              ("L", "limits.lo"),
                              ("LL", "limits.lo_lo"))
            if isinstance((value := _value(bound, key)), (int, float))
        }
        self.field_value = _value(bound, "field.value")
        self.pulse_value = _value(bound, "tuning.pulse")
        self.time_units = _value(bound, "tuning.time_units")
        self.filter_value = _value(bound, "tuning.filter")
        self.alarm_hysteresis = _value(bound, "limits.hys")
        self.simulate = bool(_value(bound, "simulate"))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        scale, ox, oy = self._transform()
        painter.fillRect(self.rect(), self.BG)
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        painter.fillRect(QRectF(0, 0, 308, 385), self.BG_ALT)
        self._header(painter)
        self._pv_bar(painter)
        self._fields(painter)
        self._trend_plot(painter)
        self._return_button(painter)
        painter.end()

    def _header(self, painter: QPainter) -> None:
        self._text(painter, QRectF(4, 4, 300, 16),
                   self.state.eyebrow, 9, self.TEXT)
        self._text(painter, QRectF(4, 19, 300, 27),
                   self.state.title, 17, self.TEXT)
        self._text(painter, QRectF(3, 50, 82, 22), "OUT", 11,
                   self.TEXT, left=True)
        self._text(painter, QRectF(3, 69, 82, 20),
                   self._display(self.field_value), 13, self.TEXT,
                   bold=True, left=True)
        self._text(painter, QRectF(95, 48, 110, 25),
                   "#######" if self.pv.bad else self._display(self.pv.value),
                   17, self.VALUE, bold=True)
        self._text(painter, QRectF(93, 70, 114, 18),
                   self.pv_units, 10, self.TEXT)

    def _pv_bar(self, painter: QPainter) -> None:
        scale_lane = QRectF(40, 94, 20, 190)
        channel = QRectF(60, 94, 22, 190)
        painter.fillRect(scale_lane, self._furniture(Role.SURFACE_SUNK, 204, 207, 213))
        painter.fillRect(channel, QColor(155, 173, 198))
        painter.setPen(QPen(self._furniture(Role.LINE, 181, 184, 190), 0.8))
        for index in range(11):
            y = scale_lane.top() + scale_lane.height() * index / 10
            length = 18 if index % 5 == 0 else 10
            painter.drawLine(QPointF(scale_lane.right() - length, y),
                             QPointF(scale_lane.right(), y))
        if not self.pv.bad and self.pv.fraction is not None:
            height = channel.height() * self.pv.fraction
            painter.fillRect(QRectF(channel.center().x() - 5.5,
                                    channel.bottom() - height,
                                    11, height), self.BLUE)
        painter.setPen(QPen(self._furniture(Role.LINE, 220, 222, 226), 0.8))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(scale_lane.united(channel))
        for limit in self.pv.ticks.values():
            fraction = self.pv._fraction_of(limit)
            if fraction is None:
                continue
            y = channel.bottom() - channel.height() * fraction
            painter.setBrush(self.TEXT)
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygonF([
                QPointF(channel.right(), y),
                QPointF(channel.right() + 5, y - 3),
                QPointF(channel.right() + 5, y + 3),
            ]))
        self._text(painter, QRectF(17, 282, 86, 17),
                   self._display(self.state.pv_min), 10, self.TEXT,
                   bold=True)

    def _fields(self, painter: QPainter) -> None:
        x_label, x_value = 91, 198
        self._text(painter, QRectF(x_label, 91, 62, 18), "Limits", 11,
                   self.TEXT, bold=True, left=True)
        rows = (
            (112, "Hi Hi Lim", self.pv.ticks.get("HH")),
            (132, "Hi Lim", self.pv.ticks.get("H")),
            (152, "Lo Lim", self.pv.ticks.get("L")),
            (172, "Lo Lo Lim", self.pv.ticks.get("LL")),
            (192, "Alm Hysteresis", self.alarm_hysteresis),
        )
        for y, caption, value in rows:
            self._text(painter, QRectF(x_label, y, 105, 17), caption, 9,
                       self.TEXT, left=True)
            self._text(painter, QRectF(x_value, y, 105, 17),
                       self._display(value), 9, self.TEXT, bold=True,
                       left=True)
        self._text(painter, QRectF(x_label, 214, 100, 18), "Simulate", 11,
                   self.TEXT, bold=True, left=True)
        self._check(painter, QRectF(94, 236, 10, 10), self.simulate)
        self._text(painter, QRectF(110, 231, 75, 19), "Simulate", 9,
                   self.TEXT, left=True)
        self._text(painter, QRectF(x_value, 231, 105, 19),
                   self._display(self.pv.value), 9, self.TEXT, bold=True,
                   left=True)
        self._text(painter, QRectF(91, 251, 90, 18), "Field Value", 9,
                   self.TEXT, left=True)
        self._text(painter, QRectF(x_value, 251, 105, 18),
                   self._display(self.field_value), 9, self.TEXT,
                   bold=True, left=True)
        self._text(painter, QRectF(91, 260, 100, 18), "Tuning", 11,
                   self.TEXT, bold=True, left=True)
        for y, caption, value, suffix in (
                (279, "PV Filter TC", self.filter_value, " s"),
                (298, "Pulse Value", self.pulse_value, " %"),
                (317, "Time Units", self.time_units, "")):
            self._text(painter, QRectF(91, y, 104, 17), caption, 9,
                       self.TEXT, left=True)
            self._text(painter, QRectF(198, y, 106, 17),
                       self._display(value) + suffix, 9, self.TEXT,
                       bold=True, left=True)

    def _trend_plot(self, painter: QPainter) -> None:
        area = QRectF(54, 329, 199, 50)
        painter.fillRect(area, self.PLOT_BG)
        plot = QRectF(78, 340, 172, 30)
        painter.setPen(QPen(self._furniture(Role.LINE, 132, 132, 132), 1))
        painter.setBrush(self.PLOT_BG)
        painter.drawRect(plot)
        self._text(painter, QRectF(54, 334, 23, 15),
                   self._display(self.state.pv_max), 9, self.VALUE,
                   left=True)
        self._text(painter, QRectF(54, 357, 23, 15),
                   self._display(self.state.pv_min), 9, self.VALUE,
                   left=True)
        values = list(self._trend)
        if len(values) > 1:
            path = QPainterPath()
            for index, value in enumerate(values):
                x = plot.left() + plot.width() * index / (len(values) - 1)
                y = plot.bottom() - self._norm(
                    value, self.state.pv_min, self.state.pv_max) * plot.height()
                path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
            painter.setPen(QPen(QColor(104, 140, 175), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    def _return_button(self, painter: QPainter) -> None:
        if "faceplate" not in self._available_actions:
            return
        draw_faceplate_icon(
            painter, QRectF(276, 16, 31, 31), "faceplate_return",
            button=True, pressed=self._pressed == "return")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._return_region().contains(
                self._design_point(event.position())) \
                and "faceplate" in self._available_actions:
            self._pressed = "return"
            self.update()
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pressed == "return":
            inside = self._return_region().contains(
                self._design_point(event.position()))
            self._pressed = None
            self.update()
            if inside and "faceplate" in self._available_actions:
                self.action_requested.emit("faceplate")
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        active = "faceplate" in self._available_actions \
            and self._return_region().contains(
                self._design_point(event.position()))
        self.setCursor(Qt.PointingHandCursor if active else Qt.ArrowCursor)
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.ArrowCursor)
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        # PI_fp has no slew/slider.  Do not inherit Loop_fp's invisible wheel
        # targets from the common painter base.
        event.ignore()

    @staticmethod
    def _return_region() -> QRectF:
        return QRectF(274, 14, 34, 34)

    @staticmethod
    def _display(value) -> str:
        if value is None:
            return "#######"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    def _check(self, painter: QPainter, rect: QRectF, checked: bool) -> None:
        painter.setPen(QPen(self._furniture(Role.LINE, 132, 135, 140), 0.8))
        painter.setBrush(self._furniture(Role.SURFACE_FIELD, 242, 243, 245))
        painter.drawRect(rect)
        if checked:
            painter.setPen(QPen(self._furniture(Role.TEXT, 70, 75, 80), 1.2))
            painter.drawLine(rect.left() + 2, rect.center().y(),
                             rect.center().x(), rect.bottom() - 2)
            painter.drawLine(rect.center().x(), rect.bottom() - 2,
                             rect.right() - 1, rect.top() + 2)


WHOLE_SURFACES = {
    "AnalogOutputFaceplate": AnalogOutputFaceplateSurface,
    "PulseInputFaceplate": PulseInputFaceplateSurface,
}


__all__ = [
    "AnalogOutputFaceplateSurface",
    "PulseInputFaceplateSurface",
    "WHOLE_SURFACES",
]
