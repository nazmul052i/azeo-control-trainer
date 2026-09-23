"""Measured Azeo AI_fp faceplate surface.

AI_fp uses the same 204-pixel module shell as the loop faceplate, but its
anatomy is deliberately different: one PV value, one PV scale, Actual mode,
trend, alarm list, unit name and five module actions.  Keeping it as one
surface prevents Qt layout size hints from moving the scale or stretching the
alarm table away from the reference figure.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPen, QPolygonF,
)

from ..theme.roles import Role
from .faceplate_icons import draw_faceplate_icon
from .loop_surface import LoopFaceplateSurface, _result


class AnalogFaceplateSurface(LoopFaceplateSurface):
    """AI_fp painted in the manual's fixed 204 x 468 coordinate system."""

    DESIGN_WIDTH = 204.0
    DESIGN_HEIGHT = 468.0
    FULL_HEIGHT = 468
    MINI_HEIGHT = 108

    # AI_fp has no associated DCC faceplate.  Retain the common action index
    # for the shared icon painter, but give it no geometry or hotspot.
    ACTIONS = ("detail", "dcc", "primary", "studio", "history", "ack")
    ACTION_CENTRES = {
        0: QPointF(18, 451),
        2: QPointF(58, 451),
        3: QPointF(98, 451),
        4: QPointF(138, 451),
        5: QPointF(178, 451),
    }

    # The reference places the Actual-mode badge, value and drop arrow in
    # three adjacent lanes.  Letting the value rect run under the arrow made
    # four-character modes such as AUTO look like ``AUT▼`` on Windows.
    # Keeping these as explicit geometry also makes the fixed-coordinate
    # contract independently testable without depending on host font metrics.
    ACTUAL_MODE_BADGE_RECT = QRectF(126, 190, 14, 17)
    ACTUAL_MODE_TEXT_RECT = QRectF(144, 188, 47, 21)
    ACTUAL_MODE_ARROW = (
        QPointF(195, 196),
        QPointF(203, 196),
        QPointF(199, 202),
    )

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.breached = ""
        self.setFixedSize(204, self.FULL_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(204, self.MINI_HEIGHT if self._mini else self.FULL_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_identity(self, path: str,
                     description: str = "Analog Input") -> None:
        module, separator, block = str(path).partition("/")
        self.set_labels(
            eyebrow=str(path) if separator else "",
            title=module or description,
            subtitle=description,
            unit_name=module or block,
        )

    def set_mini(self, mini: bool) -> None:
        self._mini = bool(mini)
        self.setFixedSize(
            204, self.MINI_HEIGHT if self._mini else self.FULL_HEIGHT)
        self.updateGeometry()
        self.update()

    def pv_geometry(self) -> tuple[QRectF, QRectF]:
        """The scale lane is left of, and adjacent to, the PV channel."""
        return QRectF(79, 110, 20, 201), QRectF(99, 110, 22, 201)

    def inner_indicator_geometry(self) -> QRectF:
        """The dark PV bar rises inside the centre of the light channel."""
        _scale, channel = self.pv_geometry()
        fraction = self._norm(
            self.state.pv, self.state.pv_min, self.state.pv_max)
        top = channel.bottom() - channel.height() * fraction
        width = 11.0
        return QRectF(
            channel.center().x() - width / 2, top,
            width, channel.bottom() - top,
        )

    @classmethod
    def actual_mode_geometry(cls) -> tuple[QRectF, QRectF, QRectF]:
        """Return the badge, text and arrow bounds in design coordinates."""
        arrow_bounds = QPolygonF(cls.ACTUAL_MODE_ARROW).boundingRect()
        return (
            QRectF(cls.ACTUAL_MODE_BADGE_RECT),
            QRectF(cls.ACTUAL_MODE_TEXT_RECT),
            arrow_bounds,
        )

    def refresh(self, bound) -> None:
        super().refresh(bound)
        # This is an indicator in AI_fp.  Simulation is configured in AI_dt;
        # making the circular S clickable here would invent a write affordance.
        self._simulate_writable = False
        primary = _result(bound, "pv.value")
        self.breached = {
            "HI_HI": "HH", "HI": "H", "LO": "L", "LO_LO": "LL",
        }.get(str(getattr(primary, "alarm_condition", "")).upper(), "")

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        scale, offset_x, offset_y = self._transform()
        painter.fillRect(self.rect(), self.BG)
        painter.translate(offset_x, offset_y)
        painter.scale(scale, scale)

        design_height = self.MINI_HEIGHT if self._mini else self.DESIGN_HEIGHT
        background = QLinearGradient(0, 0, self.DESIGN_WIDTH, 0)
        background.setColorAt(0.0, self.BG)
        background.setColorAt(0.52, self.BG_ALT)
        background.setColorAt(1.0, self.BG)
        painter.fillRect(
            QRectF(0, 0, self.DESIGN_WIDTH, design_height),
            QBrush(background),
        )
        self._analog_header(painter)
        if not self._mini:
            self._analog_vertical_bar(painter)
            self._actual_mode(painter)
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

    def _analog_header(self, painter: QPainter) -> None:
        self._text(
            painter, QRectF(2, 1, 200, 15), self.state.eyebrow,
            9, self.TEXT,
        )
        self._text(
            painter, QRectF(1, 16, 202, 29), self.state.title,
            17, self.TEXT,
        )
        self._text(
            painter, QRectF(3, 43, 198, 18), self.state.subtitle,
            10, self.TEXT,
        )
        value_text = "#######" if self.pv.bad else self._value(self.state.pv)
        self._text(
            painter, QRectF(28, 63, 106, 23), value_text,
            19, self.VALUE, bold=True,
        )
        self._text(
            painter, QRectF(28, 83, 106, 18), self.pv_units,
            12, self.TEXT,
        )

        if self.state.simulate_active:
            draw_faceplate_icon(painter, QRectF(175.5, 65.5, 19, 19), "simulate")
        painter.setPen(QPen(self._furniture(Role.LINE, 154, 154, 154), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([
            QPointF(184, 101), QPointF(188, 96), QPointF(192, 101),
        ]))

    def _analog_vertical_bar(self, painter: QPainter) -> None:
        scale_lane, channel = self.pv_geometry()
        top, bottom = channel.top(), channel.bottom()
        self._text(
            painter, QRectF(42, 94, 80, 17),
            self._range(self.state.pv_max), 13, self.TEXT, bold=True,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._furniture(Role.SURFACE_SUNK, 204, 207, 213))
        painter.drawRect(scale_lane)

        light = QColor(
            round(self.BG.red() * 0.72 + self.BLUE.red() * 0.28),
            round(self.BG.green() * 0.72 + self.BLUE.green() * 0.28),
            round(self.BG.blue() * 0.72 + self.BLUE.blue() * 0.28),
        )
        track = QLinearGradient(channel.left(), 0, channel.right(), 0)
        track.setColorAt(0, light.lighter(108))
        track.setColorAt(1, light)
        painter.fillRect(channel, QBrush(track))

        painter.setPen(QPen(self._furniture(Role.LINE, 181, 184, 190), 0.8))
        for index in range(11):
            y = top + channel.height() * index / 10
            length = 18 if index % 5 == 0 else (
                13 if index % 2 == 0 else 9)
            painter.drawLine(
                QPointF(scale_lane.right() - length, y),
                QPointF(scale_lane.right(), y),
            )

        if not self.pv.bad:
            indicator = self.inner_indicator_geometry()
            fill = QLinearGradient(
                indicator.left(), 0, indicator.right(), 0)
            fill.setColorAt(0, QColor(76, 119, 162))
            fill.setColorAt(1, QColor(60, 98, 145))
            painter.fillRect(indicator, QBrush(fill))

        painter.setPen(QPen(self._furniture(Role.LINE, 220, 222, 226), 0.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(
            scale_lane.left(), top,
            scale_lane.width() + channel.width(), channel.height(),
        ))
        for name, limit in self.pv.ticks.items():
            y = bottom - self._norm(
                limit, self.state.pv_min,
                self.state.pv_max) * channel.height()
            colour = self._furniture(Role.TEXT, 55, 55, 55)
            if name == self.breached:
                colour = QColor(self._palette[
                    Role.ALARM_P1 if name in ("HH", "LL")
                    else Role.ALARM_P2])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawPolygon(QPolygonF([
                QPointF(channel.right(), y),
                QPointF(channel.right() + 5, y - 3),
                QPointF(channel.right() + 5, y + 3),
            ]))
        self._text(
            painter, QRectF(42, 311, 80, 20),
            self._range(self.state.pv_min), 13, self.TEXT, bold=True,
        )

    def _actual_mode(self, painter: QPainter) -> None:
        if not self.mode:
            return
        rect = self.ACTUAL_MODE_BADGE_RECT
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, QColor(0, 99, 164))
        gradient.setColorAt(0.5, QColor(0, 137, 204))
        gradient.setColorAt(1, QColor(0, 126, 190))
        painter.setPen(QPen(QColor(183, 211, 225), 1))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(rect)
        self._text(
            painter, rect.adjusted(0, -1, 0, 1), "!",
            13, QColor(238, 246, 250), bold=True,
        )
        self._text(
            painter, self.ACTUAL_MODE_TEXT_RECT, self.mode,
            15, self.TEXT, bold=True, left=True,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._furniture(Role.TEXT, 40, 40, 40))
        painter.drawPolygon(QPolygonF(self.ACTUAL_MODE_ARROW))

    def _analog_footer(self, painter: QPainter) -> None:
        self._text(
            painter, QRectF(1, 420, 202, 18),
            f"Unit: {self.state.unit_name}", 13, self.TEXT, left=True,
        )
        for index, centre in self.ACTION_CENTRES.items():
            if self.ACTIONS[index] in self._available_actions:
                super()._round_button(painter, centre, index)

    @staticmethod
    def _region(name: str) -> QRectF | None:
        regions = {
            "mini": QRectF(177, 91, 27, 20),
            "alarm_up": QRectF(187, 387, 14, 14),
            "alarm_down": QRectF(187, 404, 14, 14),
        }
        if name.startswith("icon_"):
            index = int(name.split("_")[1])
            centre = AnalogFaceplateSurface.ACTION_CENTRES.get(index)
            if centre is None:
                return None
            return QRectF(centre.x() - 17, centre.y() - 17, 34, 34)
        return regions.get(name)

    def _hit(self, point: QPointF) -> str | None:
        names = ["mini", "alarm_up", "alarm_down"]
        names.extend(
            f"icon_{index}"
            for index, action in enumerate(self.ACTIONS)
            if action in self._available_actions
            and index in self.ACTION_CENTRES
        )
        for name in names:
            if name == "alarm_up" and self._alarm_offset <= 0:
                continue
            if name == "alarm_down" and (
                    self._alarm_offset >= len(self.state.alarms) - 1):
                continue
            rect = self._region(name)
            if rect is not None and rect.contains(point):
                return name
        return None


__all__ = ["AnalogFaceplateSurface"]
