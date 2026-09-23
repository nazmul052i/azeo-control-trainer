"""P&ID graphics items.

Each item binds to zero or more tags and repaints itself from a snapshot dict of
``{tag: (value, quality, ts)}``. Nothing here touches the tag database directly,
so the drawing layer can be exercised from a static dict in a test.

Symbols follow ISA-5.1 shapes closely enough to be read by anyone who reads
drawings: bowtie valves with the actuator on a stem, circles for instruments with
the tag inside and a horizontal bar for a shared-display function, a circle with
a discharge nozzle for centrifugal pumps.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QPainter, QPainterPath, QPen,
                           QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

from . import theme

Snapshot = Dict[str, Tuple[object, int, float]]


def _val(snap: Snapshot, tag: str, default: float = 0.0) -> float:
    entry = snap.get(tag)
    if entry is None:
        return default
    try:
        return float(entry[0])
    except (TypeError, ValueError):
        return default


def _qual(snap: Snapshot, tag: str) -> int:
    entry = snap.get(tag)
    return int(entry[1]) if entry else 0


def _bool(snap: Snapshot, tag: str) -> bool:
    entry = snap.get(tag)
    return bool(entry[0]) if entry else False


class PidItem(QGraphicsObject):
    """Base for every drawable. Subclasses implement ``paint`` and ``bounds``."""

    def __init__(self, x: float, y: float, tag: str = "", label: str = "") -> None:
        super().__init__()
        self.setPos(x, y)
        self.tag = tag
        self.label = label
        self.snap: Snapshot = {}
        self.clicked: Optional[Callable[[str], None]] = None
        self._w, self._h = 60.0, 60.0
        if tag:
            self.setAcceptHoverEvents(True)
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(tag)

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2 - 4, -self._h / 2 - 4, self._w + 8, self._h + 8)

    def refresh(self, snap: Snapshot) -> None:
        self.snap = snap
        self.update()

    def mousePressEvent(self, event) -> None:
        # only a left click opens; a right click belongs to the context menu
        if (event.button() == Qt.LeftButton and self.clicked and self.tag):
            self.clicked(self.tag)
            event.accept()
        else:
            super().mousePressEvent(event)

    # ---------------------------------------------------------------- utilities
    @staticmethod
    def _pen(colour: QColor = theme.OUTLINE, width: float = 1.6) -> QPen:
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _caption(self, painter: QPainter, text: str, dy: float,
                 colour: QColor = theme.NORMAL_TEXT, bold: bool = False,
                 size: int = 9) -> None:
        painter.setPen(QPen(colour))
        painter.setFont(theme.font(size, bold))
        painter.drawText(QRectF(-84, dy, 168, 16), Qt.AlignCenter, text)


# --------------------------------------------------------------------- vessels
class VesselItem(PidItem):
    """Vertical or horizontal drum with elliptical heads and a live level fill."""

    def __init__(self, x, y, w, h, label, level_tag: str = "",
                 horizontal: bool = False, sub: str = "") -> None:
        super().__init__(x, y, level_tag, label)
        self._w, self._h = w, h
        self.horizontal = horizontal
        self.level_tag = level_tag
        self.sub = sub

    def _shell(self) -> QPainterPath:
        w, h = self._w, self._h
        path = QPainterPath()
        if self.horizontal:
            r = h / 2
            path.moveTo(-w / 2 + r, -h / 2)
            path.lineTo(w / 2 - r, -h / 2)
            path.arcTo(QRectF(w / 2 - 2 * r, -h / 2, 2 * r, h), 90, -180)
            path.lineTo(-w / 2 + r, h / 2)
            path.arcTo(QRectF(-w / 2, -h / 2, 2 * r, h), 270, -180)
        else:
            # Semicircular heads, so a vertical vessel reads as a capsule the
            # way it is drawn on a real sheet. Clamped for short vessels, where
            # a full semicircle would leave no straight shell.
            r = min(w / 2, h * 0.32)
            path.moveTo(-w / 2, -h / 2 + r)
            path.arcTo(QRectF(-w / 2, -h / 2, w, 2 * r), 180, -180)
            path.lineTo(w / 2, h / 2 - r)
            path.arcTo(QRectF(-w / 2, h / 2 - 2 * r, w, 2 * r), 0, -180)
        path.closeSubpath()
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        shell = self._shell()
        # Body fill, then a heavy white stroke to lift it off the sheet, then a
        # thin grey edge to define it. Drawn in that order so the white reads as
        # a halo rather than an outline, and the fill hides pipes routed behind.
        painter.setPen(self._pen(theme.EQUIP_WHITE, 4.0))
        painter.setBrush(QBrush(theme.EQUIP_BODY))
        painter.drawPath(shell)

        if self.level_tag:
            pct = max(0.0, min(100.0, _val(self.snap, self.level_tag)))
            if pct > 0.4:
                fill_h = self._h * pct / 100.0
                clip = QPainterPath()
                clip.addRect(QRectF(-self._w / 2, self._h / 2 - fill_h,
                                    self._w, fill_h))
                painter.save()
                painter.setClipPath(shell.intersected(clip))
                painter.setBrush(QBrush(theme.LIQUID))
                painter.setPen(Qt.NoPen)
                painter.drawPath(shell)
                painter.restore()
                painter.setPen(QPen(theme.LIQUID_DARK, 1.6))
                painter.drawLine(QPointF(-self._w / 2 + 2, self._h / 2 - fill_h),
                                 QPointF(self._w / 2 - 2, self._h / 2 - fill_h))

        # Edge last, so the level fill cannot wash it out.
        painter.setPen(self._pen(theme.EQUIP_EDGE, 1.1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(shell)

        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(13, True))
        painter.drawText(QRectF(-self._w / 2, -17, self._w, 19),
                         Qt.AlignCenter, self.label)
        if self.sub:
            painter.setFont(theme.font(9))
            painter.setPen(QPen(theme.MUTED_TEXT))
            painter.drawText(QRectF(-self._w / 2 - 20, 3, self._w + 40, 16),
                             Qt.AlignCenter, self.sub)


# ----------------------------------------------------------------------- pumps
class PumpItem(PidItem):
    """Centrifugal pump: circle with a discharge nozzle and a baseplate."""

    def __init__(self, x, y, label, run_tag: str = "", fault_tag: str = "",
                 speed_tag: str = "", radius: float = 17.0,
                 speed_eu: str = "%") -> None:
        super().__init__(x, y, run_tag, label)
        self.r = radius
        self._w = self._h = radius * 2 + 16
        self.run_tag, self.fault_tag, self.speed_tag = run_tag, fault_tag, speed_tag
        self.speed_eu = speed_eu

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        running = _bool(self.snap, self.run_tag)
        faulted = _bool(self.snap, self.fault_tag)

        painter.setPen(self._pen(theme.OUTLINE, 2.0))
        painter.setBrush(QBrush(theme.EQUIP_FILL if not running else QColor("#DFEADD")))
        painter.drawEllipse(QPointF(0, 0), self.r, self.r)

        nozzle = QPolygonF([QPointF(0, -self.r), QPointF(self.r + 7, -self.r),
                            QPointF(self.r + 7, -self.r + 9), QPointF(0, -2)])
        painter.setBrush(QBrush(theme.EQUIP_FILL))
        painter.drawPolygon(nozzle)

        painter.setPen(self._pen(theme.OUTLINE, 1.4))
        painter.drawLine(QPointF(-self.r - 3, self.r + 5), QPointF(self.r + 3, self.r + 5))
        painter.drawLine(QPointF(-self.r * 0.6, self.r * 0.8),
                         QPointF(-self.r * 0.6, self.r + 5))
        painter.drawLine(QPointF(self.r * 0.6, self.r * 0.8),
                         QPointF(self.r * 0.6, self.r + 5))

        # Impeller, rotated by a fixed angle when running so the state reads at a
        # glance without relying on colour alone.
        painter.setPen(self._pen(theme.MUTED_TEXT, 1.2))
        ang = 35.0 if running else 0.0
        for k in range(3):
            a = math.radians(ang + k * 120)
            painter.drawLine(QPointF(0, 0),
                             QPointF(self.r * 0.68 * math.cos(a),
                                     self.r * 0.68 * math.sin(a)))

        if faulted:
            painter.setPen(QPen(theme.ALARM_CRITICAL, 2.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(0, 0), self.r + 4, self.r + 4)

        colour = theme.ALARM_CRITICAL if faulted else (
            theme.RUNNING if running else theme.MUTED_TEXT)
        self._caption(painter, self.label, self.r + 9, colour, bold=True, size=10)
        if self.speed_tag and running:
            self._caption(painter,
                          f"{_val(self.snap, self.speed_tag):.0f} {self.speed_eu}",
                          self.r + 25, theme.MUTED_TEXT, size=9)


# ---------------------------------------------------------------------- valves
class ValveItem(PidItem):
    """Bowtie body with a selectable actuator.

    ``kind`` is one of ``control`` (diaphragm), ``mov`` (motor), ``manual``
    (handwheel) or ``sdv`` (solenoid block).
    """

    LABEL_W = 150.0

    def __init__(self, x, y, label, kind: str = "control",
                 position_tag: str = "", zso_tag: str = "", zsc_tag: str = "",
                 fault_tag: str = "", size: float = 22.0,
                 rotation: float = 0.0, label_pos: str = "below") -> None:
        super().__init__(x, y, position_tag or zso_tag, label)
        self.kind = kind
        self.size = size
        self._w, self._h = size * 2, size * 2.6
        self.position_tag = position_tag
        self.zso_tag, self.zsc_tag, self.fault_tag = zso_tag, zsc_tag, fault_tag
        # A valve on a vertical run must caption itself to one side, otherwise
        # the pipe is drawn straight through its own label.
        self.label_pos = label_pos
        self.setRotation(rotation)

    def boundingRect(self) -> QRectF:
        s = self.size
        pad = s + self.LABEL_W + 20
        if self.label_pos == "right":
            return QRectF(-s * 1.7, -s * 1.7, s * 1.7 + pad, s * 3.4)
        if self.label_pos == "left":
            return QRectF(-pad, -s * 1.7, pad + s * 1.7, s * 3.4)
        return QRectF(-s * 1.8, -s * 1.7, s * 3.6, s * 3.8)

    def _label_rects(self) -> Tuple[QRectF, QRectF, object]:
        """Rectangles for the tag and the state text, and their alignment."""
        s = self.size
        gap = s + 12.0                    # clear of the bowtie body and stem
        if self.label_pos == "right":
            return (QRectF(gap, -16, self.LABEL_W, 15),
                    QRectF(gap, -1, self.LABEL_W, 15),
                    Qt.AlignLeft | Qt.AlignVCenter)
        if self.label_pos == "left":
            x = -gap - self.LABEL_W
            return (QRectF(x, -16, self.LABEL_W, 15),
                    QRectF(x, -1, self.LABEL_W, 15),
                    Qt.AlignRight | Qt.AlignVCenter)
        return (QRectF(-72, s * 0.72, 144, 15),
                QRectF(-72, s * 0.72 + 14, 144, 15),
                Qt.AlignCenter)

    def _state(self) -> Tuple[str, QColor]:
        if self.fault_tag and _bool(self.snap, self.fault_tag):
            return "TRIP", theme.ALARM_CRITICAL
        if self.zso_tag or self.zsc_tag:
            zso, zsc = _bool(self.snap, self.zso_tag), _bool(self.snap, self.zsc_tag)
            if zso and not zsc:
                return "OPEN", theme.OUTLINE
            if zsc and not zso:
                return "SHUT", theme.OUTLINE
            return "TRAV", theme.ALARM_HIGH
        return "", theme.OUTLINE

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        s = self.size
        state, colour = self._state()

        body = QPolygonF([QPointF(-s, -s * 0.62), QPointF(0, 0),
                          QPointF(-s, s * 0.62)])
        body2 = QPolygonF([QPointF(s, -s * 0.62), QPointF(0, 0),
                           QPointF(s, s * 0.62)])

        fill = theme.EQUIP_FILL
        if self.position_tag:
            pos = max(0.0, min(100.0, _val(self.snap, self.position_tag)))
            shade = int(250 - 95 * pos / 100.0)
            fill = QColor(shade, shade, shade)
        elif state == "SHUT":
            fill = theme.EQUIP_FILL_DARK
        elif state == "OPEN":
            fill = theme.EQUIP_WHITE

        painter.setPen(self._pen(colour, 1.9))
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(body)
        painter.drawPolygon(body2)

        painter.drawLine(QPointF(0, 0), QPointF(0, -s * 0.95))
        top = -s * 0.95
        if self.kind == "control":
            painter.setBrush(QBrush(theme.EQUIP_FILL))
            painter.drawChord(QRectF(-s * 0.62, top - s * 0.5, s * 1.24, s * 0.9),
                              0, 180 * 16)
            painter.drawLine(QPointF(-s * 0.62, top - s * 0.05),
                             QPointF(s * 0.62, top - s * 0.05))
        elif self.kind == "mov":
            painter.setBrush(QBrush(theme.EQUIP_FILL))
            painter.drawEllipse(QPointF(0, top - s * 0.34), s * 0.42, s * 0.42)
            painter.setPen(QPen(colour))
            painter.setFont(theme.font(8, True))
            painter.drawText(QRectF(-s * 0.42, top - s * 0.34 - 7, s * 0.84, 14),
                             Qt.AlignCenter, "M")
        elif self.kind == "manual":
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(-s * 0.55, top), QPointF(s * 0.55, top))
            painter.drawArc(QRectF(-s * 0.55, top - s * 0.22, s * 1.1, s * 0.44),
                            0, 180 * 16)
        else:  # sdv
            painter.setBrush(QBrush(theme.EQUIP_FILL))
            painter.drawRect(QRectF(-s * 0.4, top - s * 0.55, s * 0.8, s * 0.55))
            painter.setPen(QPen(colour))
            painter.setFont(theme.font(7, True))
            painter.drawText(QRectF(-s * 0.4, top - s * 0.55, s * 0.8, s * 0.55),
                             Qt.AlignCenter, "S")

        label_rect, state_rect, align = self._label_rects()
        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(9, True))
        painter.drawText(label_rect, align, self.label)
        if self.position_tag:
            painter.setPen(QPen(theme.MUTED_TEXT))
            painter.setFont(theme.font(9))
            painter.drawText(state_rect, align,
                             f"{_val(self.snap, self.position_tag):.0f} %")
        elif state:
            painter.setPen(QPen(colour))
            painter.setFont(theme.font(9, state == "TRAV"))
            painter.drawText(state_rect, align, state)


# ------------------------------------------------------------------ fired heater
class FiredHeaterItem(PidItem):
    """Box heater with a radiant coil, burners and a stack."""

    def __init__(self, x, y, w, h, label, fuel_tag="", flame_tag="",
                 duty_caption="") -> None:
        super().__init__(x, y, flame_tag, label)
        self._w, self._h = w + 40, h + 70
        self.bw, self.bh = w, h
        self.fuel_tag, self.flame_tag = fuel_tag, flame_tag
        self.duty_caption = duty_caption

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.bw, self.bh
        lit = _bool(self.snap, self.flame_tag)

        painter.setPen(self._pen(theme.EQUIP_WHITE, 4.0))
        painter.setBrush(QBrush(theme.EQUIP_BODY))
        painter.drawRect(QRectF(w / 2 - 34, -h / 2 - 46, 22, 46))     # stack
        painter.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), 6, 6)
        painter.setPen(self._pen(theme.EQUIP_EDGE, 1.1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), 6, 6)

        # radiant coil
        painter.setPen(self._pen(theme.PROCESS_LINE, 3.0))
        painter.setBrush(Qt.NoBrush)
        coil = QPainterPath()
        x0, x1 = -w / 2 + 16, w / 2 - 48
        rows, top, span = 4, -h / 2 + 46, h - 92
        coil.moveTo(x0 - 16, top)
        coil.lineTo(x0, top)
        for i in range(rows):
            y = top + i * span / (rows - 1)
            if i % 2 == 0:
                coil.lineTo(x1, y)
                if i < rows - 1:
                    coil.arcTo(QRectF(x1 - 9, y, 18, span / (rows - 1)), 90, -180)
            else:
                coil.lineTo(x0, y)
                if i < rows - 1:
                    coil.arcTo(QRectF(x0 - 9, y, 18, span / (rows - 1)), 90, 180)
        coil.lineTo(x1 + 28, top + span)
        painter.drawPath(coil)

        # burners
        for bx in (-w / 4, w / 4):
            painter.setPen(self._pen(theme.OUTLINE, 1.4))
            painter.setBrush(QBrush(theme.EQUIP_FILL))
            painter.drawRect(QRectF(bx - 11, h / 2 - 13, 22, 13))
            if lit:
                flame = QPainterPath()
                flame.moveTo(bx - 8, h / 2 - 13)
                flame.quadTo(bx - 3, h / 2 - 30, bx, h / 2 - 38)
                flame.quadTo(bx + 3, h / 2 - 30, bx + 8, h / 2 - 13)
                flame.closeSubpath()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(theme.FLAME))
                painter.drawPath(flame)

        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(14, True))
        painter.drawText(QRectF(-w / 2, -h / 2 + 5, w, 21), Qt.AlignCenter, self.label)
        painter.setFont(theme.font(9, True))
        painter.setPen(QPen(theme.RUNNING if lit else theme.ALARM_CRITICAL))
        painter.drawText(QRectF(-w / 2, -h / 2 + 27, w, 15), Qt.AlignCenter,
                         "FIRING" if lit else "NO FLAME")


# ------------------------------------------------------------------------ pipes
class PipeItem(QGraphicsItem):
    """Orthogonal pipe run with an optional flow arrow."""

    def __init__(self, points: List[Tuple[float, float]], width: float = 3.0,
                 colour: QColor = None, dashed: bool = False,
                 arrow_at: float = 0.55) -> None:
        super().__init__()
        self.pts = [QPointF(*p) for p in points]
        self.width = width
        self.colour = colour or theme.PROCESS_LINE
        self.dashed = dashed
        self.arrow_at = arrow_at
        self.setZValue(-10)

    def boundingRect(self) -> QRectF:
        xs = [p.x() for p in self.pts]
        ys = [p.y() for p in self.pts]
        return QRectF(min(xs) - 12, min(ys) - 12,
                      max(xs) - min(xs) + 24, max(ys) - min(ys) + 24)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self.colour, self.width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if self.dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        path = QPainterPath(self.pts[0])
        for p in self.pts[1:]:
            path.lineTo(p)
        painter.drawPath(path)

        if self.arrow_at is None or len(self.pts) < 2:
            return
        total = sum(math.hypot(self.pts[i + 1].x() - self.pts[i].x(),
                               self.pts[i + 1].y() - self.pts[i].y())
                    for i in range(len(self.pts) - 1))
        target, walked = total * self.arrow_at, 0.0
        for i in range(len(self.pts) - 1):
            a, b = self.pts[i], self.pts[i + 1]
            seg = math.hypot(b.x() - a.x(), b.y() - a.y())
            if walked + seg >= target and seg > 1e-6:
                f = (target - walked) / seg
                px = a.x() + (b.x() - a.x()) * f
                py = a.y() + (b.y() - a.y()) * f
                ang = math.atan2(b.y() - a.y(), b.x() - a.x())
                head = QPolygonF([
                    QPointF(px + 7 * math.cos(ang), py + 7 * math.sin(ang)),
                    QPointF(px - 4 * math.cos(ang) + 4.5 * math.sin(ang),
                            py - 4 * math.sin(ang) - 4.5 * math.cos(ang)),
                    QPointF(px - 4 * math.cos(ang) - 4.5 * math.sin(ang),
                            py - 4 * math.sin(ang) + 4.5 * math.cos(ang))])
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(self.colour))
                painter.drawPolygon(head)
                return
            walked += seg


# ------------------------------------------------------- instruments and values
class InstrumentBubble(PidItem):
    """ISA instrument balloon. A horizontal bar marks a shared display function."""

    def __init__(self, x, y, tag: str, shared: bool = True, radius: float = 19.0,
                 leader_to: Optional[Tuple[float, float]] = None) -> None:
        super().__init__(x, y, tag, tag)
        self.r = radius
        self.shared = shared
        self._w = self._h = radius * 2 + 4
        self.leader_to = leader_to
        self.setZValue(5)

    def boundingRect(self) -> QRectF:
        base = QRectF(-self.r - 3, -self.r - 3, 2 * self.r + 6, 2 * self.r + 6)
        if self.leader_to:
            return base.united(QRectF(QPointF(0, 0),
                                      QPointF(*self.leader_to)).normalized()
                               .adjusted(-3, -3, 3, 3))
        return base

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.leader_to:
            pen = QPen(theme.SIGNAL_LINE, 1.0)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(0, 0), QPointF(*self.leader_to))

        quality = _qual(self.snap, self.tag)
        edge = theme.BAD_QUALITY if quality == 2 else (
            theme.UNCERTAIN if quality == 1 else theme.OUTLINE)
        painter.setPen(self._pen(edge, 2.0 if quality else 1.6))
        painter.setBrush(QBrush(theme.VALUE_BG))
        painter.drawEllipse(QPointF(0, 0), self.r, self.r)
        if self.shared:
            painter.drawLine(QPointF(-self.r, 0), QPointF(self.r, 0))

        parts = self.tag.split("-", 1)
        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(9, True))
        painter.drawText(QRectF(-self.r, -self.r + 2, 2 * self.r, self.r),
                         Qt.AlignCenter, parts[0])
        painter.setFont(theme.font(8))
        painter.drawText(QRectF(-self.r, 0, 2 * self.r, self.r),
                         Qt.AlignCenter, parts[1] if len(parts) > 1 else "")


class ValueBox(PidItem):
    """Live readout: description, value with units, and a quality marker."""

    def __init__(self, x, y, tag: str, eu: str = "", decimals: int = 1,
                 width: float = 96.0, caption: str = "") -> None:
        super().__init__(x, y, tag, tag)
        self.eu = eu
        self.decimals = decimals
        self.caption = caption
        self._w, self._h = width, 38.0
        self.setZValue(6)

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2, -self._h / 2, self._w, self._h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        quality = _qual(self.snap, self.tag)
        entry = self.snap.get(self.tag)

        painter.setPen(QPen(theme.VALUE_BORDER, 1.0))
        painter.setBrush(QBrush(theme.VALUE_BG))
        painter.drawRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5))

        painter.setPen(QPen(theme.MUTED_TEXT))
        painter.setFont(theme.font(8))
        painter.drawText(QRectF(-self._w / 2 + 4, -self._h / 2 + 2, self._w - 8, 13),
                         Qt.AlignLeft | Qt.AlignVCenter, self.caption or self.tag)

        if entry is None:
            text, colour = "-- no tag --", theme.ALARM_CRITICAL
        elif isinstance(entry[0], bool):
            text, colour = ("ON" if entry[0] else "OFF"), theme.quality_colour(quality)
        else:
            text = f"{float(entry[0]):.{self.decimals}f} {self.eu}".strip()
            colour = theme.quality_colour(quality)

        painter.setPen(QPen(colour))
        painter.setFont(theme.font(13, True, mono=True))
        painter.drawText(QRectF(-self._w / 2 + 4, -self._h / 2 + 14, self._w - 8, 21),
                         Qt.AlignLeft | Qt.AlignVCenter, text)

        if quality:
            painter.setPen(QPen(theme.quality_colour(quality)))
            painter.setFont(theme.font(8, True))
            painter.drawText(QRectF(-self._w / 2, -self._h / 2 + 2, self._w - 5, 13),
                             Qt.AlignRight | Qt.AlignVCenter,
                             "BAD" if quality == 2 else "UNC")


class StatusLamp(PidItem):
    """Discrete indicator with an explicit text state, never colour alone."""

    def __init__(self, x, y, tag: str, caption: str, on_text: str = "ON",
                 off_text: str = "OFF", alarm_when_on: bool = False) -> None:
        super().__init__(x, y, tag, caption)
        self.caption = caption
        self.on_text, self.off_text = on_text, off_text
        self.alarm_when_on = alarm_when_on
        # Wide enough for the longest caption in use ("Control room ESD")
        # without it running into the right-aligned state text.
        self._w, self._h = 178.0, 21.0
        self.setZValue(6)

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2, -self._h / 2, self._w, self._h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        on = _bool(self.snap, self.tag)
        colour = (theme.ALARM_CRITICAL if self.alarm_when_on else theme.RUNNING) \
            if on else theme.MUTED_TEXT

        painter.setPen(QPen(theme.VALUE_BORDER, 1.0))
        painter.setBrush(QBrush(theme.VALUE_BG))
        painter.drawRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5))
        painter.setPen(QPen(colour, 1.2))
        painter.setBrush(QBrush(colour if on else theme.EQUIP_FILL))
        painter.drawEllipse(QPointF(-self._w / 2 + 10, 0), 4.8, 4.8)

        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(8))
        painter.drawText(QRectF(-self._w / 2 + 20, -self._h / 2, 108, self._h),
                         Qt.AlignLeft | Qt.AlignVCenter, self.caption)
        painter.setPen(QPen(colour))
        painter.setFont(theme.font(8, True))
        painter.drawText(QRectF(-self._w / 2, -self._h / 2, self._w - 4, self._h),
                         Qt.AlignRight | Qt.AlignVCenter,
                         self.on_text if on else self.off_text)


class LegendItem(QGraphicsItem):
    """Compact drawing legend in a white bordered box, drafting style."""

    W, H = 178.0, 118.0

    def __init__(self, x: float, y: float) -> None:
        super().__init__()
        self.setPos(x, y)
        self.setZValue(4)

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, self.W + 4, self.H + 4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(theme.OUTLINE, 1.2))
        painter.setBrush(QBrush(theme.VALUE_BG))
        painter.drawRect(QRectF(0, 0, self.W, self.H))

        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(8, True))
        painter.drawText(QRectF(0, 4, self.W, 14), Qt.AlignHCenter, "LEGEND")

        y0, dy, sx0, sx1, tx = 34.0, 22.0, 12.0, 46.0, 56.0

        # process pipe
        pen = QPen(theme.PROCESS_LINE, 3.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(sx0, y0), QPointF(sx1, y0))
        # instrument signal
        pen = QPen(theme.SIGNAL_LINE, 1.2)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(sx0, y0 + dy), QPointF(sx1, y0 + dy))
        # valve bowtie
        cy = y0 + 2 * dy
        cx = (sx0 + sx1) / 2
        painter.setPen(QPen(theme.OUTLINE, 1.4))
        painter.setBrush(QBrush(theme.EQUIP_FILL))
        painter.drawPolygon(QPolygonF([QPointF(cx - 11, cy - 7), QPointF(cx, cy),
                                       QPointF(cx - 11, cy + 7)]))
        painter.drawPolygon(QPolygonF([QPointF(cx + 11, cy - 7), QPointF(cx, cy),
                                       QPointF(cx + 11, cy + 7)]))
        # instrument bubble
        cy = y0 + 3 * dy
        painter.drawEllipse(QPointF(cx, cy), 9, 9)
        painter.drawLine(QPointF(cx - 9, cy), QPointF(cx + 9, cy))

        painter.setPen(QPen(theme.NORMAL_TEXT))
        painter.setFont(theme.font(7))
        for i, caption in enumerate(["Process pipe", "Instrument signal",
                                     "Valve", "Instrument"]):
            painter.drawText(QRectF(tx, y0 + i * dy - 7, self.W - tx - 4, 14),
                             Qt.AlignLeft | Qt.AlignVCenter, caption)


class LabelItem(QGraphicsItem):
    """Static annotation: titles, stream names, notes."""

    def __init__(self, x, y, text: str, size: int = 9, bold: bool = False,
                 colour: QColor = None, align=Qt.AlignLeft, width: float = 240) -> None:
        super().__init__()
        self.setPos(x, y)
        self.text, self.size, self.bold = text, size, bold
        self.colour = colour or theme.MUTED_TEXT
        self.align = align
        self.width = width

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -10, self.width + 8, self.size * 1.8 + 8)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(QPen(self.colour))
        painter.setFont(theme.font(self.size, self.bold))
        painter.drawText(QRectF(0, -8, self.width, self.size * 1.8),
                         self.align | Qt.AlignVCenter, self.text)
