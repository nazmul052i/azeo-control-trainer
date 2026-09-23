"""ISA-101 operator graphics, Azeo high-performance style.

The presentation follows the high-performance HMI conventions the reference
screenshot uses: a near-grey canvas, equipment in muted blue-grey with no
decoration, process lines that recede, and colour reserved for what needs the
operator's eye - alarm markers, the selected element's border, and the green
output bars. Every controlled variable appears as the compact inline loop box
(PV over >SP over an OUT bar); clicking it selects it and opens the loop
faceplate, clicking a pump or bare transmitter opens the tag faceplate.

Values come live from the tag database snapshot and the attached control
system every refresh; nothing on the display is static text.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsObject, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from . import theme

Snapshot = Dict[str, tuple]

# ------------------------------------------------------------ ISA-101 palette
CANVAS = QColor("#E8ECF1")
BAND = QColor("#DDE0E4")
# the high-performance silver-blue equipment treatment: light cool
# body with cylindrical shading, soft steel edges
EQUIP = QColor("#E2E9EF")
EQUIP_DARK = QColor("#BCCBD8")
EQUIP_EDGE = QColor("#93A7B8")
LINE = QColor("#7C8FA0")
TEXT = QColor("#2E3A44")
MUTED = QColor("#6B7883")
VALUE = QColor("#123C63")
FIELD = QColor("#F4F5F6")
FIELD_EDGE = QColor("#9AA6B0")
LEVEL = QColor("#5B7FA6")
OUT_BAR = QColor("#0E6B5C")
SELECT = QColor("#7030A0")
AL_HI = QColor("#E8A317")
AL_CRIT = QColor("#C1272D")
RUN_DARK = QColor("#3D4F5D")


def _f(size: float, bold: bool = False) -> QFont:
    f = QFont("Arial", 1)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


class HmiItem(QGraphicsObject):
    """Base drawable with selection, click routing and live refresh."""

    def __init__(self, x: float, y: float, ref: str = "") -> None:
        super().__init__()
        self.setPos(x, y)
        self.ref = ref                     # tag or module this element opens
        self.snap: Snapshot = {}
        self.scene_ref = None
        self._w, self._h = 60.0, 60.0
        if ref:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"Open {ref[6:]}" if ref.startswith("SCENE:")
                            else ref)

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2 - 6, -self._h / 2 - 6,
                      self._w + 12, self._h + 12)

    def refresh(self, snap: Snapshot) -> None:
        self.snap = snap
        self.update()

    def val(self, tag: str, default: float = 0.0) -> float:
        row = self.snap.get(tag)
        try:
            return float(row[0]) if row else default
        except (TypeError, ValueError):
            return default

    def mousePressEvent(self, ev) -> None:
        if getattr(self.scene_ref, "edit_mode", False):
            # on a builder canvas the item moves instead of opening
            super().mousePressEvent(ev)
            return
        if self.scene_ref is not None:
            self.scene_ref.select(self)
        # only a left click opens; a right click selects and leaves the
        # rest to contextMenuEvent
        if (ev.button() == Qt.LeftButton and self.ref
                and self.scene_ref is not None):
            self.scene_ref.open_ref(self.ref)
        ev.accept()

    def contextMenuEvent(self, ev) -> None:  # noqa: N802
        if getattr(self.scene_ref, "edit_mode", False):
            ev.ignore()
            return
        provider = getattr(self.scene_ref, "context_provider", None)
        if provider is None or not self.ref:
            ev.ignore()
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        for entry in provider(self.ref):
            if entry is None:
                menu.addSeparator()
                continue
            text, cb, enabled = entry
            act = menu.addAction(text)
            act.setEnabled(bool(enabled))
            act.triggered.connect(lambda _c=False, f=cb: f())
        if menu.actions():
            menu.exec(ev.screenPos())
        ev.accept()

    def draw_selection(self, p: QPainter, r: QRectF) -> None:
        if self.scene_ref is not None and self.scene_ref.selected is self:
            p.setPen(QPen(SELECT, 2.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-4, -4, 4, 4), 4, 4)


# ------------------------------------------------------------------ equipment
class Equip(HmiItem):
    """A vessel, column, drum or tank in the flat ISA style.

    ``kind``: 'vessel' | 'column' | 'hvessel' | 'tank' | 'hx' | 'heater'.
    A level tag paints the inner bar the way the reference draws its towers.
    """

    def __init__(self, x, y, w, h, number: str, name: str, kind: str = "vessel",
                 level_tag: str = "", level_span=(0.0, 100.0),
                 packing: bool = False, ref: str = "",
                 name_dy: float = 0.05, name_at: str = "in",
                 trays: int = 0, outline: bool = False) -> None:
        self.name_dy = name_dy
        self.name_at = name_at
        self.trays = trays
        self.outline = outline
        super().__init__(x, y, ref or level_tag)
        self._w, self._h = w, h
        self.number, self.name = number, name
        self.kind, self.level_tag = kind, level_tag
        self.level_span = level_span
        self.packing = packing

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2 - 160, -self._h / 2 - 16,
                      self._w + 320, self._h + 34)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self._w, self._h
        r = QRectF(-w / 2, -h / 2, w, h)
        if self.outline:
            # the L1 drawing style: white body, confident navy outline
            p.setPen(QPen(NAVY, 2.0))
            p.setBrush(QBrush(PAPER))
        else:
            grad = QLinearGradient(r.topLeft(), r.topRight())
            grad.setColorAt(0.0, EQUIP_DARK)
            grad.setColorAt(0.35, EQUIP)
            grad.setColorAt(0.62, EQUIP)
            grad.setColorAt(1.0, EQUIP_DARK)
            p.setPen(QPen(EQUIP_EDGE, 1.1))
            p.setBrush(QBrush(grad))

        if self.kind == "comp":
            # centrifugal compressor: the trapezoid of the L1 drawing
            path = QPainterPath()
            path.moveTo(-w / 2, -h / 2)
            path.lineTo(w / 2, -h * 0.28)
            path.lineTo(w / 2, h * 0.28)
            path.lineTo(-w / 2, h / 2)
            path.closeSubpath()
            p.drawPath(path)
        elif self.kind == "hx":
            p.drawEllipse(r)
            p.setPen(QPen(EQUIP_EDGE, 1.4))
            p.drawLine(QPointF(-w / 2, h * 0.18), QPointF(-w * 0.12, -h * 0.22))
            p.drawLine(QPointF(-w * 0.12, -h * 0.22), QPointF(w * 0.12, h * 0.22))
            p.drawLine(QPointF(w * 0.12, h * 0.22), QPointF(w / 2, -h * 0.18))
        elif self.kind == "heater":
            path = QPainterPath()
            path.moveTo(-w / 2, h / 2)
            path.lineTo(-w / 2, -h * 0.1)
            path.lineTo(-w * 0.28, -h / 2)
            path.lineTo(w * 0.28, -h / 2)
            path.lineTo(w / 2, -h * 0.1)
            path.lineTo(w / 2, h / 2)
            path.closeSubpath()
            p.drawPath(path)
            # burner flame hint, muted
            p.setBrush(QBrush(QColor("#D8B36A")))
            p.setPen(Qt.NoPen)
            flame = QPolygonF([QPointF(-8, h / 2 - 6), QPointF(0, h * 0.1),
                               QPointF(8, h / 2 - 6)])
            p.drawPolygon(flame)
        else:
            radius = min(w * 0.28, 18.0) if self.kind != "tank" else 6.0
            p.drawRoundedRect(r, radius, radius)

        if self.trays:
            # Tray internals: alternating half-width weir lines, the way
            # the reference column drawing shows them.
            p.setPen(QPen(EQUIP_EDGE, 1.0))
            top, bot = -h * 0.36, h * 0.40
            for i in range(self.trays):
                y = top + (bot - top) * i / max(self.trays - 1, 1)
                if i % 2 == 0:
                    p.drawLine(QPointF(-w / 2 + 3, y), QPointF(w * 0.18, y))
                else:
                    p.drawLine(QPointF(-w * 0.18, y), QPointF(w / 2 - 3, y))

        if self.packing:
            p.setPen(QPen(EQUIP_EDGE, 1.0))
            top = QRectF(-w / 2 + 2, -h / 2 + 4, w - 4, h * 0.16)
            step = 7.0
            x = top.left() - top.height()
            while x < top.right():
                p.drawLine(QPointF(max(x, top.left()), top.bottom()),
                           QPointF(min(x + top.height(), top.right()), top.top()))
                x += step
            p.drawLine(top.bottomLeft(), top.bottomRight())

        # level bar inside the shell
        if self.level_tag:
            lo, hi = self.level_span
            f = (self.val(self.level_tag) - lo) / max(hi - lo, 1e-9)
            f = max(0.0, min(1.0, f))
            bar = QRectF(w * 0.12, -h * 0.42, w * 0.22, h * 0.84)
            p.setPen(QPen(QColor("#A8B8C6"), 0.8))
            p.setBrush(QBrush(QColor("#F2F5F8")))
            p.drawRect(bar)
            fill = QRectF(bar.left(), bar.bottom() - bar.height() * f,
                          bar.width(), bar.height() * f)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(LEVEL))
            p.drawRect(fill)

        p.setPen(QPen(MUTED))
        p.setFont(_f(8))
        p.drawText(QRectF(-w, -h / 2 - 2, 2 * w, 12), Qt.AlignCenter, self.number)
        p.setPen(QPen(TEXT))
        p.setFont(_f(8.5))
        if self.name_at == "left":       # beside the shell, clear of the bar
            p.drawText(QRectF(-w / 2 - 154, -7, 150, 14),
                       Qt.AlignRight | Qt.AlignVCenter, self.name)
        elif self.name_at == "right":
            p.drawText(QRectF(w / 2 + 4, -7, 150, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, self.name)
        elif self.name_at == "below":
            p.drawText(QRectF(-w * 1.4, h / 2 + 3, 2.8 * w, 14),
                       Qt.AlignHCenter, self.name)
        else:
            p.drawText(QRectF(-w * 1.4, h * self.name_dy, 2.8 * w, 30),
                       Qt.AlignHCenter, self.name)
        self.draw_selection(p, QRectF(-w / 2, -h / 2, w, h))


class PumpISA(HmiItem):
    """Centrifugal pump with the little N/S (or W/E) state boxes beside it."""

    def __init__(self, x, y, run_tag: str, ref: str = "",
                 pair: Tuple[str, str] = ("N", "S"), size: float = 26.0,
                 flip: bool = False) -> None:
        super().__init__(x, y, ref or run_tag)
        self.run_tag = run_tag
        self.pair = pair
        self._w = self._h = size
        self.flip = flip

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self._w
        running = bool(self.snap.get(self.run_tag, (0,))[0]) if self.run_tag else False
        body = RUN_DARK if running else EQUIP
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(body))
        p.drawEllipse(QRectF(-s / 2, -s / 2, s, s))
        d = -1.0 if self.flip else 1.0
        tri = QPolygonF([QPointF(-d * s * 0.32, -s * 0.30),
                         QPointF(d * s * 0.55, 0),
                         QPointF(-d * s * 0.32, s * 0.30)])
        p.setBrush(QBrush(QColor("#FFFFFF") if running else EQUIP_DARK))
        p.drawPolygon(tri)
        # the paired duty/standby letters underneath, filled when that one runs
        p.setFont(_f(6.5, True))
        for i, letter in enumerate(self.pair):
            box = QRectF(-s * 0.55 + i * s * 0.58, s / 2 + 3, s * 0.5, 10)
            on = running if i == 0 else False
            p.setPen(QPen(EQUIP_EDGE, 0.8))
            p.setBrush(QBrush(RUN_DARK if on else FIELD))
            p.drawRect(box)
            p.setPen(QPen(QColor("#FFFFFF") if on else MUTED))
            p.drawText(box, Qt.AlignCenter, letter)
        self.draw_selection(p, QRectF(-s / 2, -s / 2, s, s + 14))


class ValveISA(HmiItem):
    """Control valve with its AU/MA mode letters, read from the loop."""

    def __init__(self, x, y, pos_tag: str, controller=None,
                 vertical: bool = False, show_pct: bool = False) -> None:
        super().__init__(x, y, pos_tag)
        self.pos_tag = pos_tag
        self.controller = controller
        self.vertical = vertical
        self.show_pct = show_pct
        self._w, self._h = 30.0, 46.0

    def _mode_letters(self) -> str:
        if self.controller is None:
            return ""
        loop = self.controller.loop_for_tag(self.pos_tag)
        if loop is None:
            return ""
        from ..control.pid import Mode
        return {"Man": "MA", "Auto": "AU", "Cas": "CAS",
                "ROut": "RO"}.get(loop.pid.actual_mode.value, "")

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        pos = self.val(self.pos_tag, 0.0)
        open_ = pos > 2.0
        body = QColor("#FFFFFF") if open_ else RUN_DARK
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(body))
        w = 22.0
        if self.vertical:
            # flow is vertical: the body triangles point up and down and
            # the actuator sits to the LEFT, clear of the line.
            p.drawPolygon(QPolygonF([QPointF(-7, -w / 2), QPointF(0, 0),
                                     QPointF(7, -w / 2)]))
            p.drawPolygon(QPolygonF([QPointF(-7, w / 2), QPointF(0, 0),
                                     QPointF(7, w / 2)]))
            p.drawLine(QPointF(0, 0), QPointF(-10, 0))
            p.drawEllipse(QRectF(-19, -6, 9, 12))
        else:
            p.drawPolygon(QPolygonF([QPointF(-w / 2, -7), QPointF(0, 0),
                                     QPointF(-w / 2, 7)]))
            p.drawPolygon(QPolygonF([QPointF(w / 2, -7), QPointF(0, 0),
                                     QPointF(w / 2, 7)]))
            p.drawLine(QPointF(0, 0), QPointF(0, -10))
            p.drawEllipse(QRectF(-6, -19, 12, 9))
        letters = self._mode_letters()
        if letters:
            p.setPen(QPen(MUTED))
            p.setFont(_f(6.5, True))
            if self.vertical:
                p.drawText(QRectF(-52, -20, 30, 10), Qt.AlignRight, letters)
            else:
                p.drawText(QRectF(-30, -26, 60, 10), Qt.AlignCenter, letters)
        if self.show_pct:
            p.setPen(QPen(TEXT))
            p.setFont(_f(6.5))
            if self.vertical:
                p.drawText(QRectF(-52, 2, 30, 10), Qt.AlignRight,
                           f"{pos:.1f}%")
            else:
                p.drawText(QRectF(-30, 9, 60, 10), Qt.AlignCenter,
                           f"{pos:.1f}%")
        self.draw_selection(p, QRectF(-14, -20, 28, 30))


class MovISA(HmiItem):
    """Motor operated valve, state read from its limit switches: open is a
    white bowtie, closed dark, neither limit made paints it half-travelled.
    Clicking opens the device faceplate through the ZSO tag."""

    def __init__(self, x, y, base: str) -> None:
        super().__init__(x, y, f"ZSO-{base}")
        self.base = base
        self._w, self._h = 30.0, 30.0

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        zso = bool(self.snap.get(f"ZSO-{self.base}", (0,))[0])
        zsc = bool(self.snap.get(f"ZSC-{self.base}", (0,))[0])
        body = (QColor("#FFFFFF") if zso else
                RUN_DARK if zsc else AL_HI)
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(body))
        w = 22.0
        p.drawPolygon(QPolygonF([QPointF(-w / 2, -7), QPointF(0, 0),
                                 QPointF(-w / 2, 7)]))
        p.drawPolygon(QPolygonF([QPointF(w / 2, -7), QPointF(0, 0),
                                 QPointF(w / 2, 7)]))
        # the motor actuator: a small box with M on the stem
        p.drawLine(QPointF(0, 0), QPointF(0, -9))
        p.setBrush(QBrush(FIELD))
        p.drawRect(QRectF(-6, -19, 12, 10))
        p.setPen(QPen(TEXT))
        p.setFont(_f(6.5, True))
        p.drawText(QRectF(-6, -19, 12, 10), Qt.AlignCenter, "M")
        self.draw_selection(p, QRectF(-15, -20, 30, 28))


class FanISA(HmiItem):
    """A fan: circle with blades, coloured by its running feedback."""

    def __init__(self, x, y, tag: str, run_tag: str) -> None:
        super().__init__(x, y, run_tag)
        self.tag_name = tag
        self.run_tag = run_tag
        self._w = self._h = 32.0

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        running = bool(self.snap.get(self.run_tag, (0,))[0])
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(RUN_DARK if running else EQUIP))
        p.drawEllipse(QRectF(-15, -15, 30, 30))
        p.setPen(QPen(QColor("#FFFFFF") if running else EQUIP_DARK, 2.0))
        for dx, dy in ((0, -12), (10.5, 6), (-10.5, 6)):
            p.drawLine(QPointF(0, 0), QPointF(dx, dy))
        p.setPen(QPen(MUTED))
        p.setFont(_f(6.5))
        p.drawText(QRectF(-40, -30, 80, 11), Qt.AlignCenter, self.tag_name)
        self.draw_selection(p, QRectF(-15, -15, 30, 30))


class EsdBanner(HmiItem):
    """SIS status: quiet while healthy, red with the first-out cause when
    any ESD latch is standing."""

    def __init__(self, x, y, controller) -> None:
        super().__init__(x, y, "")
        self.controller = controller
        self._w, self._h = 258.0, 26.0

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        esd = getattr(self.controller, "esd", None)
        active = esd is not None and any(esd.latched.values())
        open_loop = not getattr(self.controller, "enabled", True)
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        p.setPen(QPen(FIELD_EDGE, 1.0))
        p.setBrush(QBrush(AL_CRIT if active else
                          AL_HI if open_loop else FIELD))
        p.drawRect(r)
        p.setPen(QPen(QColor("#FFFFFF") if active else MUTED))
        p.setFont(_f(7.5 if active else 8, bold=active))
        if active:
            from ..control.esd import CAUSES
            fo = esd.first_out or next(
                (c for c, v in esd.latched.items() if v), "?")
            desc = next((c[2] for c in CAUSES if c[0] == fo), "")
            text = f"ESD {fo}: {desc}"[:47]
        elif open_loop:
            text = "OPEN LOOP - CONTROL PASSIVE, SIS ACTIVE"
        else:
            text = "SIS HEALTHY"
        p.drawText(r, Qt.AlignCenter, text)


NAVY = QColor("#24407C")
PAPER = QColor("#F8FAFD")


class TextBox(HmiItem):
    """The L1 drawing's boxed stream label: white card, navy border,
    centred wrapped text. Clickable when given a ref."""

    def __init__(self, x, y, text: str, w: float = 118.0, h: float = 46.0,
                 ref: str = "", bold: bool = True) -> None:
        super().__init__(x, y, ref)
        self.text = text
        self.bold = bold
        self._w, self._h = w, h

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        p.setPen(QPen(NAVY, 1.6))
        p.setBrush(QBrush(PAPER))
        p.drawRect(r)
        p.setPen(QPen(NAVY))
        p.setFont(_f(7.2, self.bold))
        p.drawText(r.adjusted(4, 2, -4, -2),
                   Qt.AlignCenter | Qt.TextWordWrap, self.text)
        self.draw_selection(p, r)


class VBar(HmiItem):
    """An ISA analog-indicator dynamo: the PV scale with its EU figures
    and graduations, the alarm zones shaded on the track with HI HI/HI/
    LO/LO LO limit markers, the working-SP pointer on the right edge and
    the dark PV column - placed beside the vessel it measures."""

    def __init__(self, x, y, tag: str, unit: str = "%",
                 span=(0.0, 100.0), height: float = 90.0,
                 decimals: int = 0, limits: Optional[Dict] = None,
                 sp_fn: Optional[Callable[[], float]] = None) -> None:
        super().__init__(x, y, tag)
        self.tag_name, self.unit_txt = tag, unit
        self.span, self.dec = span, decimals
        self.limits = dict(limits or {})
        self.sp_fn = sp_fn
        self._w, self._h = 60.0, height + 34

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        bar_h = self._h - 34
        bar = QRectF(-2, -self._h / 2 + 14, 16, bar_h)
        lo, hi = self.span
        rng = max(hi - lo, 1e-9)

        def y_of(value: float) -> float:
            f = max(0.0, min(1.0, (value - lo) / rng))
            return bar.bottom() - f * bar.height()

        # track, with each configured alarm zone shaded behind the scale
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(PAPER))
        p.drawRect(bar)
        for key, col, above in (("HI", "#EEDCAC", True),
                                ("LO", "#EEDCAC", False),
                                ("HI_HI", "#E2B0B0", True),
                                ("LO_LO", "#E2B0B0", False)):
            if key in self.limits:
                yl = y_of(self.limits[key])
                zone = (QRectF(bar.left(), bar.top(), bar.width(),
                               yl - bar.top()) if above else
                        QRectF(bar.left(), yl, bar.width(),
                               bar.bottom() - yl))
                p.setBrush(QBrush(QColor(col)))
                p.drawRect(zone)

        # the PV column, dark on the right half of the track
        v = self.val(self.tag_name)
        yv = y_of(v)
        p.setBrush(QBrush(QColor("#3F6AA0")))
        p.drawRect(QRectF(bar.left() + 7.5, yv, bar.width() - 8.7,
                          max(bar.bottom() - yv - 1.0, 0.0)))

        # graduations and the EU figures of the PV scale
        p.setPen(QPen(NAVY, 0.9))
        for i in range(11):
            yt = bar.top() + bar.height() * i / 10.0
            ln = 6.0 if i % 5 == 0 else 3.5
            p.drawLine(QPointF(bar.left() - ln, yt), QPointF(bar.left(), yt))
        p.setFont(_f(4.8))
        p.drawText(QRectF(bar.left() - 28, bar.top() - 4, 26, 8),
                   Qt.AlignRight | Qt.AlignVCenter, f"{hi:g}")
        p.drawText(QRectF(bar.left() - 28, bar.bottom() - 4, 26, 8),
                   Qt.AlignRight | Qt.AlignVCenter, f"{lo:g}")

        # alarm limit markers across the track
        for key, col in (("HI", "#C07A00"), ("LO", "#C07A00"),
                         ("HI_HI", "#B33A3A"), ("LO_LO", "#B33A3A")):
            if key in self.limits:
                yl = y_of(self.limits[key])
                p.setPen(QPen(QColor(col), 1.6))
                p.drawLine(QPointF(bar.left(), yl),
                           QPointF(bar.right() + 3, yl))

        # working-SP pointer on the right edge
        if self.sp_fn is not None:
            try:
                ys = y_of(float(self.sp_fn()))
            except Exception:
                ys = None
            if ys is not None:
                p.setPen(QPen(NAVY, 1.0))
                p.setBrush(QBrush(NAVY))
                p.drawPolygon(QPolygonF([
                    QPointF(bar.right() + 1.5, ys),
                    QPointF(bar.right() + 8.5, ys - 4.0),
                    QPointF(bar.right() + 8.5, ys + 4.0)]))

        p.setPen(QPen(NAVY, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar)
        p.setPen(QPen(NAVY))
        p.setFont(_f(6.2))
        p.drawText(QRectF(-34, -self._h / 2, 68, 12), Qt.AlignCenter,
                   self.tag_name)
        p.setFont(_f(7.2, True))
        p.drawText(QRectF(-34, self._h / 2 - 16, 68, 14), Qt.AlignCenter,
                   f"{v:.{self.dec}f} {self.unit_txt}")
        self.draw_selection(p, bar)


class Junction(HmiItem):
    """A mixing or splitting point drawn as a captioned circle, the way
    the reference overview marks its stream headers."""

    def __init__(self, x, y, text: str, radius: float = 30.0) -> None:
        super().__init__(x, y, "")
        self.text = text
        self._w = self._h = radius * 2

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        p.setPen(QPen(NAVY, 1.8))
        p.setBrush(QBrush(PAPER))
        p.drawEllipse(r)
        p.setPen(QPen(NAVY))
        p.setFont(_f(6.4, True))
        p.drawText(r.adjusted(2, 2, -2, -2),
                   Qt.AlignCenter | Qt.TextWordWrap, self.text)


class TrendTile(HmiItem):
    """The reference's bottom-row tile: a caption, PV/SP/OUT and a live
    sparkline. History accumulates at the display refresh rate."""

    N = 150

    def __init__(self, x, y, ref: str, caption: str, unit: str,
                 controller=None, tag: str = "",
                 mini: bool = False) -> None:
        super().__init__(x, y, ref)
        self.caption, self.unit_txt = caption, unit
        self.controller = controller
        self.mini = mini
        self.module = ref if (controller is not None
                              and ref in getattr(controller, "loops", {})) \
            else ""
        self.tag_name = tag or ref
        self._hist: List[float] = []
        self._sp: List[float] = []
        self._w, self._h = (140.0, 74.0) if mini else (186.0, 92.0)

    def _loop(self):
        if self.module:
            return self.controller.loops[self.module].pid
        return None

    def refresh(self, snap: Snapshot) -> None:
        pid = self._loop()
        if pid is not None:
            pv, sp = pid.pv, pid.sp_wrk
        else:
            row = snap.get(self.tag_name)
            try:
                pv = float(row[0]) if row else 0.0
            except (TypeError, ValueError):
                pv = 0.0
            sp = pv
        self._hist.append(float(pv))
        self._sp.append(float(sp))
        if len(self._hist) > self.N:
            del self._hist[0]
            del self._sp[0]
        super().refresh(snap)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self._w, self._h
        r = QRectF(-w / 2, -h / 2, w, h)
        edge = NAVY if self.mini else FIELD_EDGE
        p.setPen(QPen(edge, 1.4 if self.mini else 1.0))
        p.setBrush(QBrush(PAPER if self.mini else FIELD))
        p.drawRect(r)
        p.setPen(QPen(NAVY if self.mini else MUTED))
        p.setFont(_f(6.4, True))
        p.drawText(QRectF(r.left() + 4, r.top() + 2, w - 8, 11),
                   Qt.AlignLeft, self.caption)
        pid = self._loop()
        pv = self._hist[-1] if self._hist else 0.0
        p.setPen(QPen(NAVY if self.mini else VALUE))
        p.setFont(_f(7.6 if self.mini else 8.6, True))
        p.drawText(QRectF(r.left() + 4, r.top() + 13, w - 8, 12),
                   Qt.AlignLeft, f"{pv:.1f} {self.unit_txt}")
        if pid is not None and not self.mini:
            p.setFont(_f(6.6))
            p.setPen(QPen(MUTED))
            p.drawText(QRectF(r.left() + 4, r.top() + 26, w - 8, 10),
                       Qt.AlignLeft,
                       f"SP {pid.sp_wrk:.1f}   OUT {pid.out:.0f} %")
        # sparkline over the tail of the tile
        top = 26 if self.mini else 38
        box = QRectF(r.left() + 4, r.top() + top, w - 8, h - top - 6)
        p.setPen(QPen(FIELD_EDGE, 0.8))
        p.setBrush(QBrush(QColor("#FDFDFB")))
        p.drawRect(box)
        if len(self._hist) >= 2:
            lo = min(min(self._hist), min(self._sp))
            hi = max(max(self._hist), max(self._sp))
            span = max(hi - lo, 1e-6) * 1.2
            mid = (hi + lo) / 2.0

            def pt(series, i):
                x = box.left() + i / (self.N - 1) * box.width()
                y = (box.center().y()
                     + (mid - series[i]) / span * box.height())
                return QPointF(x, min(max(y, box.top() + 1),
                                      box.bottom() - 1))

            if pid is not None:
                p.setPen(QPen(MUTED, 0.9, Qt.DashLine))
                p.drawPolyline(QPolygonF(
                    [pt(self._sp, i) for i in range(len(self._sp))]))
            p.setPen(QPen(QColor("#2E6DA4"), 1.4))
            p.drawPolyline(QPolygonF(
                [pt(self._hist, i) for i in range(len(self._hist))]))
        self.draw_selection(p, r)


class AlarmRail(HmiItem):
    """The reference's right-hand alarm rail: critical and warning
    counts with their standing points, from the plant-wide alarm
    system when one is attached, else from the loop blocks."""

    def __init__(self, x, y, controller, alarms=None,
                 height: float = 560.0) -> None:
        super().__init__(x, y, "")
        self.controller = controller
        self.alarm_sys = alarms
        self._w, self._h = 216.0, height

    def _standing(self):
        crit, warn = [], []
        if self.alarm_sys is not None:
            for q in getattr(self.alarm_sys, "points", []):
                if not getattr(q, "active", False):
                    continue
                pri = str(getattr(q, "priority", "")).upper()
                (crit if "CRIT" in pri else warn).append(str(q.key))
        elif self.controller is not None:
            for m, loop in self.controller.loops.items():
                a = loop.pid.alarms
                if a.hi_hi or a.lo_lo:
                    crit.append(m)
                elif a.hi or a.lo:
                    warn.append(m)
        return crit, warn

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self._w, self._h
        r = QRectF(-w / 2, -h / 2, w, h)
        crit, warn = self._standing()

        def section(top: float, title: str, colour, rows, max_rows: int):
            hdr = QRectF(r.left(), top, w, 18)
            p.setPen(QPen(FIELD_EDGE, 1.0))
            p.setBrush(QBrush(colour))
            p.drawRect(hdr)
            p.setPen(QPen(QColor("#FFFFFF") if rows else MUTED))
            p.setFont(_f(7.2, True))
            p.drawText(hdr.adjusted(6, 0, -4, 0), Qt.AlignVCenter, title)
            p.setBrush(QBrush(FIELD))
            body = QRectF(r.left(), top + 18, w, max_rows * 14 + 6)
            p.setPen(QPen(FIELD_EDGE, 1.0))
            p.drawRect(body)
            p.setPen(QPen(TEXT))
            p.setFont(_f(6.8))
            for i, name in enumerate(rows[:max_rows]):
                p.drawText(QRectF(body.left() + 6, body.top() + 3 + i * 14,
                                  w - 10, 13), Qt.AlignVCenter, name)
            if not rows:
                p.setPen(QPen(MUTED))
                p.drawText(body.adjusted(6, 0, 0, 0), Qt.AlignVCenter
                           if max_rows == 1 else Qt.AlignTop | Qt.AlignLeft,
                           "  none")
            return top + 18 + max_rows * 14 + 14

        y = r.top()
        y = section(y, f"CRITICAL ALARMS ({len(crit)})",
                    AL_CRIT if crit else EQUIP_DARK, crit, 5)
        y = section(y, f"WARNING ALARMS ({len(warn)})",
                    AL_HI if warn else EQUIP_DARK, warn, 8)
        section(y, "STANDING TOTAL "
                   f"({len(crit) + len(warn)})", EQUIP_DARK,
                crit + warn, 12)


class LoopBox(HmiItem):
    """The inline loop faceplate: PV, >SP and the OUT bar."""

    def __init__(self, x, y, module: str, controller, unit: str = "",
                 card: bool = False) -> None:
        super().__init__(x, y, module)
        self.module = module
        self.controller = controller
        self.unit = unit
        self.card = card
        self._w, self._h = 118.0, 52.0
        if card:
            self._w, self._h = 122.0, 64.0

    def boundingRect(self) -> QRectF:
        # the dotted shroud and the tag label sit outside the value box
        return QRectF(-self._w / 2 - 8, -self._h / 2 - 18,
                      self._w + 16, self._h + 24)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        loop = self.controller.loops.get(self.module) if self.controller else None
        w, h = self._w, self._h
        if self.card:
            # the reference-overview card: white face, the module name in
            # a light header band INSIDE the card, no dotted shroud
            r = QRectF(-w / 2, -h / 2 + 12, w, h - 12)
            p.setPen(QPen(FIELD_EDGE, 1.0))
            p.setBrush(QBrush(FIELD))
            p.drawRect(QRectF(-w / 2, -h / 2, w, h))
            p.setBrush(QBrush(EQUIP))
            p.drawRect(QRectF(-w / 2, -h / 2, w, 12))
            p.setPen(QPen(TEXT))
            p.setFont(_f(6.6, True))
            p.drawText(QRectF(-w / 2, -h / 2, w, 12), Qt.AlignCenter,
                       self.module)
        else:
            r = QRectF(-w / 2, -h / 2, w, h)
            # The ISA-5.1 dotted shroud: this is a DCS software function,
            # not field hardware. Encloses the tag label with the box,
            # the same compact PVM drawing convention.
            shroud = QPen(MUTED, 0.9)
            shroud.setDashPattern([1.2, 2.4])
            p.setPen(shroud)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(-w / 2 - 5, -h / 2 - 15, w + 10, h + 20))
            p.setPen(QPen(FIELD_EDGE, 1.0))
            p.setBrush(QBrush(FIELD))
            p.drawRect(r)
            p.setPen(QPen(MUTED))
            p.setFont(_f(6.5))
            p.drawText(QRectF(r.left(), r.top() - 12, w, 11),
                       Qt.AlignHCenter, self.module)
        if loop is None:
            return
        pid = loop.pid

        # alarm marker, only when something is actually abnormal
        if pid.alarms.any_active() or not pid.pv_good:
            crit = pid.alarms.hi_hi or pid.alarms.lo_lo or not pid.pv_good
            p.setBrush(QBrush(AL_CRIT if crit else AL_HI))
            p.setPen(Qt.NoPen)
            tri = QPolygonF([QPointF(r.left() + 4, r.top() + 12),
                             QPointF(r.left() + 12, r.top() + 12),
                             QPointF(r.left() + 8, r.top() + 4)])
            p.drawPolygon(tri)
            p.setPen(QPen(QColor("#FFFFFF")))
            p.setFont(_f(6, True))
            p.drawText(QRectF(r.left() + 4, r.top() + 3, 8, 10),
                       Qt.AlignCenter, "!")

        p.setPen(QPen(MUTED))
        p.setFont(_f(6.5))
        p.drawText(QRectF(r.left() + 16, r.top() + 3, 22, 11), "PV:")
        p.drawText(QRectF(r.left() + 12, r.top() + 17, 26, 11), ">SP:")
        p.drawText(QRectF(r.left() + 10, r.top() + 31, 28, 11), "OUT:")

        p.setPen(QPen(VALUE))
        p.setFont(_f(8.5, True))
        p.drawText(QRectF(r.left() + 38, r.top() + 1, 52, 13),
                   Qt.AlignRight, f"{pid.pv:.2f}")
        p.setFont(_f(7.5))
        p.setPen(QPen(MUTED))
        p.drawText(QRectF(r.left() + 38, r.top() + 16, 52, 12),
                   Qt.AlignRight, f"{pid.sp_wrk:.2f}")
        p.setFont(_f(6))
        p.drawText(QRectF(r.right() - 26, r.top() + 3, 24, 10), self.unit)

        # OUT bar, green fill with the mode letters at its right
        bar = QRectF(r.left() + 38, r.top() + 33, 52, 8)
        p.setPen(QPen(FIELD_EDGE, 0.8))
        p.setBrush(QBrush(QColor("#E7E3D8")))
        p.drawRect(bar)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(OUT_BAR))
        p.drawRect(QRectF(bar.left(), bar.top(),
                          bar.width() * max(0.0, min(1.0, pid.out / 100.0)),
                          bar.height()))
        p.setPen(QPen(MUTED))
        p.setFont(_f(6, True))
        p.drawText(QRectF(bar.right() + 2, bar.top() - 1, 26, 10),
                   pid.actual_mode.value.upper()[:3])
        self.draw_selection(p, r)


class MeasBox(HmiItem):
    """A bare measurement: tag over a small white value field with units."""

    def __init__(self, x, y, tag: str, unit: str = "", decimals: int = 1) -> None:
        super().__init__(x, y, tag)
        self.tag_name, self.unit_txt, self.dec = tag, unit, decimals
        self._w, self._h = 86.0, 26.0

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self._w, self._h
        r = QRectF(-w / 2, -h / 2 + 5, w, 16)
        p.setPen(QPen(MUTED))
        p.setFont(_f(6.5))
        p.drawText(QRectF(-w / 2, -h / 2 - 7, w, 11), Qt.AlignHCenter,
                   self.tag_name)
        p.setPen(QPen(FIELD_EDGE, 1.0))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawRect(r)
        p.setPen(QPen(VALUE))
        p.setFont(_f(8, True))
        p.drawText(r.adjusted(3, 0, -26, 0), Qt.AlignRight | Qt.AlignVCenter,
                   f"{self.val(self.tag_name):.{self.dec}f}")
        p.setPen(QPen(MUTED))
        p.setFont(_f(6))
        p.drawText(r.adjusted(0, 0, -3, 0), Qt.AlignRight | Qt.AlignVCenter,
                   self.unit_txt)
        self.draw_selection(p, r)


class StateLamp(HmiItem):
    """A DI/DO dynamo: a square lamp with its caption above and the live
    state word below - dark fill with a lit dot while the discrete is
    true. Works for run contacts, limit switches, trips and commands."""

    def __init__(self, x, y, tag: str, caption: str = "",
                 on_text: str = "ON", off_text: str = "OFF",
                 size: float = 20.0, ref: str = "") -> None:
        super().__init__(x, y, ref or tag)
        self.tag_name = tag
        self.caption = caption or tag
        self.on_text, self.off_text = on_text, off_text
        self._w, self._h = max(size, 16.0) + 40, max(size, 16.0) + 30

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self._h - 30
        on = bool(self.val(self.tag_name, 0.0))
        box = QRectF(-s / 2, -s / 2, s, s)
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(RUN_DARK if on else FIELD))
        p.drawRect(box)
        if on:
            p.setPen(QPen(QColor("#FFFFFF"), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(box.adjusted(s * 0.32, s * 0.32,
                                       -s * 0.32, -s * 0.32))
        p.setPen(QPen(MUTED))
        p.setFont(_f(6.2))
        p.drawText(QRectF(-60, -self._h / 2, 120, 10), Qt.AlignCenter,
                   self.caption)
        p.setPen(QPen(TEXT))
        p.setFont(_f(7.0, True))
        p.drawText(QRectF(-60, s / 2 + 3, 120, 12), Qt.AlignCenter,
                   self.on_text if on else self.off_text)
        self.draw_selection(p, box)


_SVG_STYLE = re.compile(r"<style.*?</style>", re.S)
_SVG_PORT = re.compile(
    r'data-connection-side="(top|right|bottom|left)"\s+'
    r'data-anchor-x="([0-9.eE+-]+)"\s+data-anchor-y="([0-9.eE+-]+)"')
_SVG_PORT_NORMAL = re.compile(
    r'<circle\b[^>]*\bdata-connection-side="(top|right|bottom|left)"'
    r'[^>]*\bdata-connection-normal="(top|right|bottom|left)"')


def _svg_ports(txt: str) -> Dict[str, Tuple[float, float]]:
    """The symbol library's embedded connection anchors - normalized
    points that touch the visible geometry, not the viewBox edge."""
    names = {"top": "T", "right": "R", "bottom": "B", "left": "L"}
    out = {}
    for side, ax, ay in _SVG_PORT.findall(txt):
        try:
            out[names[side]] = (float(ax), float(ay))
        except ValueError:
            continue
    return out


def _restyle_svg(txt: str, line: str, fill: str) -> str:
    """Non-destructive recolour through the symbol's own <style>
    contract; the asset on disk is never modified."""
    m = _SVG_STYLE.search(txt)
    if m is None:
        return txt
    s = m.group(0)
    if line:
        s = re.sub(r"stroke:\s*#[0-9a-fA-F]+", f"stroke: {line}", s)
        s = re.sub(r"(\.symbol-(?:solid|text)\s*\{[^}]*?fill:\s*)"
                   r"#[0-9a-fA-F]+",
                   lambda mm: mm.group(1) + line, s)
    if fill:
        s = re.sub(r"(\.symbol-fill\s*\{[^}]*?fill:\s*)#[0-9a-fA-F]+",
                   lambda mm: mm.group(1) + fill, s)
    return txt[:m.start()] + s + txt[m.end():]


class ImageItem(HmiItem):
    """An imported graphic: SVG rendered live through QSvgRenderer,
    anything else loaded as a pixmap. Sized in scene units; when only
    one dimension is given the aspect ratio is kept. Library SVGs
    expose their embedded connection ports, and their line/fill
    colours can be overridden without touching the asset."""

    def __init__(self, x, y, path: str, w: float = 0.0,
                 h: float = 0.0, line_color: str = "",
                 fill_color: str = "") -> None:
        super().__init__(x, y, "")
        self.path = str(path)
        self.line_color, self.fill_color = line_color, fill_color
        self._svg = None
        self._pix = None
        self.ports: Dict[str, Tuple[float, float]] = {}
        self.port_normals: Dict[str, str] = {}
        nat_w, nat_h = 120.0, 120.0
        if self.path.lower().endswith(".svg"):
            try:
                from PySide6.QtSvg import QSvgRenderer
                try:
                    with open(self.path, encoding="utf-8",
                              errors="replace") as fh:
                        txt = fh.read()
                except OSError:
                    txt = ""
                if txt:
                    self.ports = _svg_ports(txt)
                    names = {"top": "T", "right": "R", "bottom": "B", "left": "L"}
                    self.port_normals = {names[side]: names[normal]
                                         for side, normal in _SVG_PORT_NORMAL.findall(txt)}
                    if line_color or fill_color:
                        txt = _restyle_svg(txt, line_color, fill_color)
                    rend = QSvgRenderer(
                        QByteArray(txt.encode("utf-8")))
                    if rend.isValid():
                        self._svg = rend
                        sz = rend.defaultSize()
                        nat_w = max(sz.width(), 1)
                        nat_h = max(sz.height(), 1)
            except ImportError:
                pass
        else:
            from PySide6.QtGui import QPixmap
            pm = QPixmap(self.path)
            if not pm.isNull():
                self._pix = pm
                nat_w, nat_h = pm.width(), pm.height()
        if w <= 0 and h <= 0:
            w, h = float(nat_w), float(nat_h)
        elif w <= 0:
            w = h * nat_w / nat_h
        elif h <= 0:
            h = w * nat_h / nat_w
        self._w, self._h = float(w), float(h)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        if self._svg is not None:
            self._svg.render(p, r)
        elif self._pix is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(r, self._pix, QRectF(self._pix.rect()))
        else:
            p.setPen(QPen(FIELD_EDGE, 1.0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setPen(QPen(MUTED))
            p.setFont(_f(6.5))
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap,
                       f"image not found\n{self.path}")
        self.draw_selection(p, r)


class Flag(QGraphicsItem):
    """Off-display stream flag, the pointed box of the reference."""

    def __init__(self, x, y, text: str, right: bool = True) -> None:
        super().__init__()
        self.setPos(x, y)
        self.text = text
        self.right = right
        self._w, self._h = max(96.0, 8.0 + 7.2 * len(text)), 20.0

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2 - 4, -self._h / 2 - 4,
                      self._w + 8, self._h + 8)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h, tip = self._w, self._h, 9.0
        if self.right:
            pts = [QPointF(-w / 2, -h / 2), QPointF(w / 2 - tip, -h / 2),
                   QPointF(w / 2, 0), QPointF(w / 2 - tip, h / 2),
                   QPointF(-w / 2, h / 2)]
        else:
            pts = [QPointF(w / 2, -h / 2), QPointF(-w / 2 + tip, -h / 2),
                   QPointF(-w / 2, 0), QPointF(-w / 2 + tip, h / 2),
                   QPointF(w / 2, h / 2)]
        p.setPen(QPen(EQUIP_EDGE, 1.2))
        p.setBrush(QBrush(BAND))
        p.drawPolygon(QPolygonF(pts))
        p.setPen(QPen(TEXT))
        p.setFont(_f(7, True))
        p.drawText(QRectF(-w / 2, -h / 2, w, h), Qt.AlignCenter, self.text)


class Label(QGraphicsItem):
    def __init__(self, x, y, text: str, size: float = 8.0, bold: bool = False,
                 colour: QColor = TEXT) -> None:
        super().__init__()
        self.setPos(x, y)
        self.text, self.size, self.bold, self.colour = text, size, bold, colour

    def boundingRect(self) -> QRectF:
        return QRectF(-160, -12, 320, 24)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setPen(QPen(self.colour))
        p.setFont(_f(self.size, self.bold))
        p.drawText(self.boundingRect(), Qt.AlignCenter, self.text)


class Pipe(QGraphicsItem):
    """Muted orthogonal process line with one arrowhead."""

    def __init__(self, pts: List[Tuple[float, float]], width: float = 2.2,
                 arrow_at: Optional[float] = 0.6,
                 colour: Optional[QColor] = None) -> None:
        super().__init__()
        self.pts = [QPointF(*p) for p in pts]
        self.width, self.arrow_at = width, arrow_at
        self.colour = colour or LINE
        self.setZValue(-10)

    def boundingRect(self) -> QRectF:
        xs = [p.x() for p in self.pts]; ys = [p.y() for p in self.pts]
        return QRectF(min(xs) - 10, min(ys) - 10,
                      max(xs) - min(xs) + 20, max(ys) - min(ys) + 20)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self.colour, self.width)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(self.pts[0])
        for q in self.pts[1:]:
            path.lineTo(q)
        p.drawPath(path)
        if self.arrow_at is None:
            return
        total = sum(math.hypot(self.pts[i+1].x()-self.pts[i].x(),
                               self.pts[i+1].y()-self.pts[i].y())
                    for i in range(len(self.pts)-1))
        walked, target = 0.0, total * self.arrow_at
        for i in range(len(self.pts)-1):
            a, b = self.pts[i], self.pts[i+1]
            seg = math.hypot(b.x()-a.x(), b.y()-a.y())
            if walked + seg >= target and seg > 1e-6:
                f = (target - walked) / seg
                x, y = a.x()+(b.x()-a.x())*f, a.y()+(b.y()-a.y())*f
                ang = math.atan2(b.y()-a.y(), b.x()-a.x())
                head = QPolygonF([
                    QPointF(x+7*math.cos(ang), y+7*math.sin(ang)),
                    QPointF(x-4*math.cos(ang)+4.2*math.sin(ang),
                            y-4*math.sin(ang)-4.2*math.cos(ang)),
                    QPointF(x-4*math.cos(ang)-4.2*math.sin(ang),
                            y-4*math.sin(ang)+4.2*math.cos(ang))])
                p.setPen(Qt.NoPen); p.setBrush(QBrush(self.colour))
                p.drawPolygon(head)
                return
            walked += seg


class Connector(QGraphicsItem):
    """A process line pinned to two symbols: each end anchors to a
    normalized point on its host's edge and follows the host when it
    moves. Routes orthogonally, leaving each symbol perpendicular to
    the anchored edge, with an arrowhead into the target."""

    def __init__(self, a_item, a_anchor, b_item, b_anchor,
                 a_side: str = "R", b_side: str = "L",
                 width: float = 2.2, arrow: bool = True,
                 navy: bool = True, ortho: bool = True,
                 bends=None, colour: str = "",
                 arrows: str = "") -> None:
        super().__init__()
        self.a_item, self.b_item = a_item, b_item
        self.a_anchor, self.b_anchor = tuple(a_anchor), tuple(b_anchor)
        self.a_side, self.b_side = a_side, b_side
        self.width, self.ortho = width, ortho
        self.arrows = arrows or ("end" if arrow else "none")
        #: either end may be another connector - a branch on a line;
        #: the anchor's first element is then the 0..1 position along
        #: the parent route
        self.a_is_line = isinstance(a_item, Connector)
        self.b_is_line = isinstance(b_item, Connector)
        #: branches re-route when this line does
        self.dependents: List["Connector"] = []
        self.bends: List[QPointF] = [QPointF(b[0], b[1])
                                     for b in (bends or [])]
        self.colour = (QColor(colour) if colour
                       else NAVY if navy else LINE)
        self.setZValue(-9)
        self._pts: List[QPointF] = []
        if self.a_is_line:
            a_item.dependents.append(self)
        if self.b_is_line:
            b_item.dependents.append(self)
        self.sync()
        for host in (a_item, b_item):
            if not hasattr(host, "xChanged"):
                continue              # a line parent notifies via sync
            host.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
            for sig in (host.xChanged, host.yChanged,
                        host.rotationChanged):
                sig.connect(self.sync)

    @staticmethod
    def _endpoint(item, anchor) -> QPointF:
        w = getattr(item, "_w", 60.0)
        h = getattr(item, "_h", 60.0)
        return item.mapToScene(QPointF(-w / 2 + anchor[0] * w,
                                       -h / 2 + anchor[1] * h))

    _SIDE_ORDER = "RBLT"      # +90 deg clockwise maps R->B->L->T

    @classmethod
    def _eff_side(cls, side: str, item, anchor=None) -> str:
        """The anchored side after the host's mirror and rotation, so
        the stub still leaves perpendicular to the real edge."""
        # A branch can be named top/bottom yet open horizontally. Use its
        # authored direction only at that nozzle, preserving free-edge edits.
        port = getattr(item, "ports", {}).get(side)
        if port is not None and (anchor is None or
                                 all(abs(a - b) < 1e-6 for a, b in zip(anchor, port))):
            side = getattr(item, "port_normals", {}).get(side, side)
        if item.transform().m11() < 0:
            side = {"L": "R", "R": "L"}.get(side, side)
        steps = int(round(item.rotation() / 90.0)) % 4
        if steps and side in cls._SIDE_ORDER:
            side = cls._SIDE_ORDER[
                (cls._SIDE_ORDER.index(side) + steps) % 4]
        return side

    @staticmethod
    def _line_point(parent: "Connector",
                    t: float) -> Tuple[QPointF, bool]:
        """Point at t (0..1 by arc length) along the parent line, and
        whether the segment there runs horizontally."""
        pts = parent._pts
        segs = [(a, b, math.hypot(b.x() - a.x(), b.y() - a.y()))
                for a, b in zip(pts, pts[1:])]
        total = sum(s[2] for s in segs) or 1.0
        walk = max(0.0, min(1.0, t)) * total
        for a, b, ln in segs:
            if walk <= ln or (a, b, ln) is segs[-1]:
                f = walk / ln if ln > 1e-9 else 0.0
                pt = QPointF(a.x() + (b.x() - a.x()) * f,
                             a.y() + (b.y() - a.y()) * f)
                return pt, abs(b.x() - a.x()) >= abs(b.y() - a.y())
            walk -= ln
        return pts[-1], True

    def _line_target(self) -> Tuple[QPointF, bool]:
        return self._line_point(self.b_item, self.b_anchor[0])

    def _route(self) -> List[QPointF]:
        if self.a_is_line:
            p1, seg_h1 = self._line_point(self.a_item,
                                          self.a_anchor[0])
            ah = not seg_h1           # leave the parent square-on
        else:
            p1 = self._endpoint(self.a_item, self.a_anchor)
            ah = self._eff_side(self.a_side, self.a_item, self.a_anchor) in "LR"
        if self.b_is_line:
            p2, seg_h = self._line_target()
            bh = not seg_h            # a branch meets its line square-on
        else:
            p2 = self._endpoint(self.b_item, self.b_anchor)
            bh = self._eff_side(self.b_side, self.b_item, self.b_anchor) in "LR"
        if not self.ortho:
            return [p1] + list(self.bends) + [p2]
        if self.bends:
            # orthogonal legs threaded through the user's bends,
            # alternating axes starting perpendicular to A's edge
            pts = [p1]
            cur, horiz = p1, ah
            for q in list(self.bends) + [p2]:
                mid = (QPointF(q.x(), cur.y()) if horiz
                       else QPointF(cur.x(), q.y()))
                if mid != cur and mid != q:
                    pts.append(mid)
                pts.append(q)
                cur = q
                horiz = not horiz
            return pts
        if ah and bh:
            mx = (p1.x() + p2.x()) / 2
            return [p1, QPointF(mx, p1.y()), QPointF(mx, p2.y()), p2]
        if not ah and not bh:
            my = (p1.y() + p2.y()) / 2
            return [p1, QPointF(p1.x(), my), QPointF(p2.x(), my), p2]
        if ah:
            return [p1, QPointF(p2.x(), p1.y()), p2]
        return [p1, QPointF(p1.x(), p2.y()), p2]

    def set_bends(self, bends) -> None:
        """Live bend update during a drag; no rebuild."""
        self.bends = [b if isinstance(b, QPointF)
                      else QPointF(b[0], b[1]) for b in bends]
        self.sync()

    def sync(self) -> None:
        self.prepareGeometryChange()
        self._pts = self._route()
        self.update()
        for d in list(self.dependents):
            try:
                d.sync()
            except RuntimeError:      # a branch that no longer exists
                self.dependents.remove(d)

    def boundingRect(self) -> QRectF:
        xs = [p.x() for p in self._pts]
        ys = [p.y() for p in self._pts]
        return QRectF(min(xs) - 10, min(ys) - 10,
                      max(xs) - min(xs) + 20, max(ys) - min(ys) + 20)

    def shape(self) -> QPainterPath:
        path = QPainterPath(self._pts[0])
        for q in self._pts[1:]:
            path.lineTo(q)
        from PySide6.QtGui import QPainterPathStroker
        st = QPainterPathStroker()
        st.setWidth(max(self.width, 8.0))
        return st.createStroke(path)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self.colour, self.width)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(self._pts[0])
        for q in self._pts[1:]:
            path.lineTo(q)
        p.drawPath(path)
        if self.b_is_line:            # the branch tees off with a dot
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(self.colour))
            p.drawEllipse(self._pts[-1], 3.4, 3.4)
        if self.a_is_line:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(self.colour))
            p.drawEllipse(self._pts[0], 3.4, 3.4)
        if self.arrows in ("end", "both"):
            self._draw_head(p, self._pts[-2], self._pts[-1])
        if self.arrows in ("start", "both"):
            self._draw_head(p, self._pts[1], self._pts[0])

    def _draw_head(self, p: QPainter, a: QPointF, b: QPointF) -> None:
        """A filled arrowhead at b, pointing from a to b."""
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if seg < 1e-6:
            return
        ux, uy = (b.x() - a.x()) / seg, (b.y() - a.y()) / seg
        # a slim P&ID dart, not a squat triangle: roughly 3:1
        ln, half = 11.0, 3.5
        tip = QPointF(b.x() - 0.5 * ux, b.y() - 0.5 * uy)
        head = QPolygonF([
            tip,
            QPointF(tip.x() - ln * ux + half * uy,
                    tip.y() - ln * uy - half * ux),
            QPointF(tip.x() - ln * ux - half * uy,
                    tip.y() - ln * uy + half * ux)])
        p.setPen(QPen(self.colour, 0.8, Qt.SolidLine, Qt.FlatCap,
                      Qt.MiterJoin))
        p.setBrush(QBrush(self.colour))
        p.drawPolygon(head)


# -------------------------------------------------------------------- scene
class SignalPath(QGraphicsItem):
    """An ISA-5.1 dashed instrument signal line, drawn under the items it
    connects so endpoints can sit at item centres without fuss."""

    def __init__(self, points) -> None:
        super().__init__()
        self.pts = [QPointF(x, y) for x, y in points]
        self.setZValue(-5)

    def boundingRect(self) -> QRectF:
        xs = [p.x() for p in self.pts]
        ys = [p.y() for p in self.pts]
        return QRectF(min(xs) - 2, min(ys) - 2,
                      max(xs) - min(xs) + 4, max(ys) - min(ys) + 4)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        pen = QPen(MUTED, 0.9)
        pen.setDashPattern([5.0, 3.0])
        p.setPen(pen)
        for a, b in zip(self.pts, self.pts[1:]):
            p.drawLine(a, b)


def wire_scene_signals(scene, controller) -> None:
    """Draw the loop connectivity the P&ID reader expects: a dashed signal
    line from every PVM to the valve it drives, from its primary element's
    measurement box, and from its cascade master. Derived from the live
    loop table, so the drawing cannot drift from the strategy."""
    if controller is None:
        return
    pvms, valves, meas = {}, {}, {}
    for it in scene.items():
        if isinstance(it, LoopBox):
            pvms[it.module] = it
        elif isinstance(it, ValveISA):
            valves[it.pos_tag] = it
        elif isinstance(it, MeasBox):
            meas[it.tag_name] = it
    if not valves:            # section grids: connectivity would be noise
        return

    def route(a, b):
        ax, ay = a.pos().x(), a.pos().y()
        bx, by = b.pos().x(), b.pos().y()
        if abs(ax - bx) < 6.0 or abs(ay - by) < 6.0:
            pts = [(ax, ay), (bx, by)]
        elif abs(ax - bx) >= abs(ay - by):
            pts = [(ax, ay), (bx, ay), (bx, by)]
        else:
            pts = [(ax, ay), (ax, by), (bx, by)]
        scene.addItem(SignalPath(pts))

    for module, pvm in pvms.items():
        loop = controller.loops.get(module)
        if loop is None:
            continue
        out_tags = ([loop.out_tag] if loop.out_tag else
                    [leg[0] for leg in loop.split])
        for tag in out_tags:
            if tag in valves:
                route(pvm, valves[tag])
        if loop.pv_tag in meas:
            route(meas[loop.pv_tag], pvm)
        if loop.master and loop.master in pvms:
            route(pvms[loop.master], pvm)


class HmiScene(QGraphicsScene):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setBackgroundBrush(QBrush(CANVAS))
        self.title = title
        self.edit_mode = False        # True on a builder canvas
        self.live: List[HmiItem] = []
        self.selected: Optional[HmiItem] = None
        self.on_open: Optional[Callable[[str], None]] = None
        #: right-click menu entries: callable(ref) -> [(text, cb, enabled)
        #: or None for a separator]; set by the operator display.
        self.context_provider = None

    def add(self, item) -> object:
        if isinstance(item, HmiItem):
            item.scene_ref = self
            self.live.append(item)
        self.addItem(item)
        return item

    def select(self, item: HmiItem) -> None:
        old, self.selected = self.selected, item
        if old is not None:
            old.update()
        item.update()

    def open_ref(self, ref: str) -> None:
        if self.on_open:
            self.on_open(ref)

    def refresh(self, snap: Snapshot) -> None:
        for item in self.live:
            item.refresh(snap)


def build_section(title: str, units, controller, db) -> HmiScene:
    """A section control display: every module of the section's units as a
    live loop box, grouped by unit, every box opening its faceplate. This
    is the layer between the overview graphic and the faceplates - the
    overview stays legible, and nothing is reachable only through a list."""
    s = HmiScene(title)
    boxes_per_row = 5
    x0, dx, dy = 140, 150, 84
    y = 60
    for code, caption in units:
        mods = [(m, lp) for m, lp in sorted(controller.loops.items())
                if lp.pv_tag in db and db[lp.pv_tag].unit == code]
        if not mods:
            continue
        s.add(Label(230, y, f"{code}  -  {caption}", 10, bold=True))
        y += 52
        for i, (m, lp) in enumerate(mods):
            col, row = i % boxes_per_row, i // boxes_per_row
            unit_txt = db[lp.pv_tag].eu if lp.pv_tag in db else ""
            s.add(LoopBox(x0 + col * dx, y + row * dy, m, controller,
                          unit_txt))
        y += ((len(mods) - 1) // boxes_per_row + 1) * dy + 18
    s.setSceneRect(QRectF(0, 0, x0 + boxes_per_row * dx, max(y + 20, 400)))
    return s


def build_column_pid(n: int, controller) -> HmiScene:
    """A detailed column P&ID in the Azeo high-performance layout: the
    tower with its tray internals, condenser, reflux drum, pumps and
    reboiler, every valve with its live position, and the unit's loop
    PVMs placed beside the equipment they act on."""
    c = f"T{n - 4}"
    s = HmiScene(f"{c} COLUMN")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))

    # ------------------------------------------------------------ the tower
    A(Equip(430, 430, 100, 480, c, "", kind="vessel", trays=14,
            ref=f"LT-{n}002"))

    # feed through the shutdown valve
    A(Flag(70, 430, "FROM D3" if n == 5 else "FROM T1", right=True))
    P([(118, 430), (247, 430)], arrow_at=None)
    A(MovISA(260, 430, f"XV{n}001"))
    P([(273, 430), (380, 430)], arrow_at=0.7)
    A(LoopBox(150, 330, f"TIC-{n}001", controller, "degC"))
    A(LoopBox(150, 540, f"PDIC-{n}001", controller, "mbar"))
    A(MeasBox(150, 610, f"PDT-{n}001", "mbar", 0))

    # ---------------------------------------------------------- overheads
    P([(430, 190), (430, 130), (612, 130)], arrow_at=None)
    A(Equip(640, 130, 56, 44, f"E-{n}01", "", kind="hx", ref=f"TT-{n}007"))
    A(Flag(560, 60, "CWS", right=True))
    P([(596, 60), (626, 60), (626, 112)], arrow_at=None)
    A(Flag(720, 60, "CWR"))
    P([(654, 112), (654, 60), (684, 60)], arrow_at=None)
    A(LoopBox(640, 215, f"FIC-{n}003", controller, "m3/h"))
    P([(668, 130), (880, 130), (880, 152)], arrow_at=0.5)
    A(Equip(880, 180, 140, 56, f"D{n}", "", kind="tank",
            level_tag=f"LT-{n}001", name_at="left"))
    A(LoopBox(1090, 152, f"PIC-{n}001", controller, "barg"))
    A(LoopBox(1090, 215, f"LIC-{n}001", controller, "%"))

    # vent, and for T1 the hot gas bypass back into the drum
    P([(920, 152), (920, 95), (975, 95)], arrow_at=None)
    A(ValveISA(990, 95, f"PCV-{n}001", controller, show_pct=True))
    P([(1005, 95), (1060, 95)], arrow_at=0.7)
    A(Flag(1105, 95, "VENT"))
    if n == 5:
        P([(770, 130), (770, 180), (807, 180)], arrow_at=None)
        A(ValveISA(788, 180, "PCV-5002", controller, show_pct=True))
        A(Label(742, 155, "HGB", 6.5))
        # the water boot with its draw
        P([(946, 208), (946, 215)], arrow_at=None)
        A(Equip(946, 226, 30, 24, "", "", kind="tank",
                level_tag="LT-5003"))
        A(LoopBox(1090, 290, "LIC-5003", controller, "%"))

    # ------------------------------------------------- reflux and product
    P([(860, 208), (860, 262)], arrow_at=None)
    A(PumpISA(860, 277, f"XS-P{n}01A-RUN", pair=("A", "B"), flip=True))
    P([(847, 277), (560, 277), (560, 235), (480, 235)], arrow_at=0.85)
    A(ValveISA(690, 277, f"FCV-{n}001", controller, show_pct=True))
    A(LoopBox(640, 350, f"FIC-{n}001", controller, "m3/h"))
    P([(800, 277), (800, 345), (1122, 345)], arrow_at=0.85)
    A(ValveISA(900, 345, f"FCV-{n}002", controller, show_pct=True))
    A(MeasBox(1000, 305, f"AT-{n}001", "mol%", 2))
    A(Flag(1170, 345, "STORAGE / T2" if n == 5 else "R2 PRODUCT"))
    A(LoopBox(970, 400, f"FIC-{n}006", controller, "m3/h"))
    A(LoopBox(1090, 400, f"AIC-{n}001", controller, "mol%"))
    A(LoopBox(760, 420, f"FIC-{n}004", controller, "m3/h"))

    # ------------------------------------------------------------ reboiler
    P([(480, 585), (700, 585), (700, 612)], arrow_at=None)
    A(Equip(700, 640, 72, 52, "", "REBOILER", kind="hx", name_dy=0.62))
    P([(664, 640), (480, 640)], arrow_at=0.8)
    A(Flag(952, 628, "MP STEAM", right=False))
    P([(904, 628), (732, 628)], arrow_at=None)
    A(ValveISA(830, 628, f"FCV-{n}004", controller, show_pct=True))
    A(Flag(952, 656, "CONDENSATE"))
    P([(732, 656), (904, 656)], arrow_at=0.8)
    A(LoopBox(870, 520, f"TDIC-{n}001", controller, "degC"))
    A(LoopBox(1010, 520, f"FIC-{n}002", controller, "t/h"))

    # ------------------------------------------------------------- bottoms
    P([(430, 670), (430, 760), (552, 760)], arrow_at=None)
    A(PumpISA(572, 760, f"XS-P{n}02A-RUN", pair=("A", "B")))
    P([(585, 760), (1134, 760)], arrow_at=0.9)
    A(ValveISA(680, 760, f"FCV-{n}003", controller, show_pct=True))
    A(MeasBox(790, 720, f"AT-{n}002", "mol%", 2))
    A(Flag(1185, 760, "RECYCLE TO D1" if n == 5 else "HVY RUNDOWN"))
    if n == 5:
        # the plant-wide VPC: T1 quality demand balancing the additive
        A(LoopBox(640, 470, "QIC-5001", controller, "%dem"))
    A(LoopBox(1000, 815, f"AIC-{n}002", controller, "mol%"))
    A(LoopBox(860, 815, f"LIC-{n}002", controller, "%"))
    A(LoopBox(720, 815, f"FIC-{n}007", controller, "m3/h"))
    A(LoopBox(450, 815, f"FIC-{n}005", controller, "m3/h"))
    return s


def build_u100_pid(controller) -> HmiScene:
    """D1 feed drum: fresh feed, the recycle return, off-spec import, and
    the charge train with its MOVs, pumps and minimum flow."""
    s = HmiScene("D1 FEED DRUM")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))
    A(Equip(430, 420, 110, 300, "D1", "FEED DRUM", level_tag="LT-1001",
            name_at="left"))
    A(Flag(70, 350, "FRESH FEED"))
    P([(118, 350), (375, 350)], arrow_at=0.7)
    A(Flag(80, 240, "RECYCLE FROM T1"))
    P([(138, 240), (247, 240)], arrow_at=None)
    A(MovISA(258, 240, "MOV1002"))
    P([(271, 240), (410, 240), (410, 270)], arrow_at=0.8)
    A(Flag(80, 460, "OFF-SPEC IMPORT"))
    P([(138, 460), (239, 460)], arrow_at=None)
    A(ValveISA(250, 460, "LCV-1001", controller, show_pct=True))
    P([(261, 460), (375, 460)], arrow_at=0.7)
    A(LoopBox(150, 545, "LIC-1002", controller, "%"))
    A(LoopBox(150, 150, "LIC-1001", controller, "%"))
    P([(430, 570), (430, 650), (517, 650)], arrow_at=None)
    A(MovISA(528, 650, "MOV1001A"))
    P([(539, 650), (592, 650)], arrow_at=None)
    A(PumpISA(605, 650, "XS-P101A-RUN", pair=("A", "B")))
    P([(618, 650), (1107, 650)], arrow_at=0.95)
    A(MovISA(718, 650, "XV1001"))
    A(ValveISA(820, 650, "FCV-1001", controller, show_pct=True))
    A(Flag(1155, 650, "TO H1"))
    P([(670, 650), (670, 545), (485, 545)], arrow_at=0.9)
    A(ValveISA(580, 545, "FCV-1002", controller, show_pct=True))
    A(LoopBox(580, 465, "FIC-1002", controller, "m3/h"))
    A(MeasBox(940, 605, "PT-1002", "barg"))
    A(LoopBox(1070, 560, "PIC-1001", controller, "barg"))
    A(LoopBox(820, 740, "FIC-1001", controller, "m3/h"))
    A(LoopBox(605, 760, "SIC-1001", controller, "%"))
    return s


def build_u200_pid(controller) -> HmiScene:
    """C1 recycle compressor: knockout drum, the machine, discharge cooler
    and the anti-surge recycle with its two constraint overrides."""
    s = HmiScene("C1 COMPRESSOR")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))
    A(Flag(80, 300, "FROM D3"))
    P([(128, 300), (247, 300)], arrow_at=None)
    A(MovISA(258, 300, "XV2001"))
    P([(271, 300), (385, 300)], arrow_at=0.7)
    A(Equip(430, 330, 90, 120, "V-201", "KO DRUM", level_tag="LT-2001",
            name_at="left"))
    P([(430, 390), (430, 460), (519, 460)], arrow_at=None)
    A(ValveISA(530, 460, "LCV-2001", controller, show_pct=True))
    P([(541, 460), (602, 460)], arrow_at=0.8)
    A(Flag(650, 460, "SOUR WATER"))
    A(LoopBox(280, 510, "LIC-2001", controller, "%"))
    P([(430, 270), (430, 220), (585, 220)], arrow_at=0.6)
    A(Equip(620, 220, 70, 70, "C1", "", kind="hx", ref="SC-2001"))
    A(Label(620, 275, "COMPRESSOR", 7.5))
    A(MeasBox(620, 335, "ST-2001", "rpm", 0))
    A(MeasBox(620, 377, "IT-2001", "A", 0))
    P([(655, 220), (760, 220), (760, 160), (892, 160)], arrow_at=None)
    A(Equip(920, 160, 56, 44, "E-201", "", kind="hx", ref="TT-2002"))
    P([(948, 160), (1122, 160)], arrow_at=0.7)
    A(Flag(1174, 160, "TO H1 FEED MIX"))
    A(Flag(850, 290, "CWS", right=True))
    P([(886, 290), (920, 290), (920, 243)], arrow_at=None)
    A(ValveISA(920, 232, "FCV-2003", controller, vertical=True,
               show_pct=True))
    P([(920, 221), (920, 182)], arrow_at=None)
    A(LoopBox(1060, 260, "TIC-2002", controller, "degC"))
    # the anti-surge recycle back to suction, high-selected overrides
    P([(760, 200), (760, 540), (340, 540), (340, 300)], arrow_at=0.85)
    A(ValveISA(560, 540, "FCV-2001", controller, show_pct=True))
    A(MeasBox(430, 610, "UY-2001", "%", 1))
    A(LoopBox(560, 650, "UIC-2001", controller, "%"))
    A(LoopBox(700, 650, "PIC-2002", controller, "barg"))
    A(LoopBox(840, 650, "TIC-2001", controller, "degC"))
    A(LoopBox(150, 150, "PIC-2001", controller, "barg"))
    A(LoopBox(290, 150, "SIC-2001", controller, "rpm"))
    A(MeasBox(760, 110, "PT-2002", "barg"))
    A(MeasBox(780, 400, "IT-2002", "A", 0))
    A(MeasBox(780, 442, "IT-2003", "A", 0))
    A(Label(780, 472, "lube oil pumps P-201 / P-202", 7))
    # H2 makeup from the battery-limit header, the loop's inventory make
    A(Flag(80, 90, "H2 HEADER"))
    P([(128, 90), (229, 90)], arrow_at=None)
    A(ValveISA(240, 90, "PCV-2003", controller, show_pct=True))
    P([(251, 90), (395, 90), (395, 268)], arrow_at=0.9)
    A(MeasBox(566, 66, "FT-2003", "kNm3/h", 2))
    A(MeasBox(566, 108, "PT-2004", "barg", 1))
    A(LoopBox(430, 150, "PIC-2003", controller, "barg"))
    # the four-handle capacity menu's field ends
    A(ValveISA(510, 220, "FCV-2002", controller, show_pct=True))
    A(ValveISA(820, 160, "FCV-2004", controller, show_pct=True))
    A(MeasBox(490, 419, "GT-2001", "deg", 1))
    A(MeasBox(620, 419, "JT-2001", "MW", 2))
    A(MeasBox(1060, 110, "AT-2001", "kg/kmol", 1))
    # the discharge serves BOTH consumers now
    P([(1035, 160), (1035, 205), (1130, 205)], arrow_at=0.8)
    A(Flag(1192, 205, "REACTOR QUENCH"))
    return s


def build_u300_pid(controller) -> HmiScene:
    """H1 charge heater: two passes with balancing, fuel gas and oil
    trains, combustion air, oxygen trim, skin override and draft."""
    s = HmiScene("H1 CHARGE HEATER")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))
    A(Equip(560, 420, 180, 260, "H1", "", kind="heater"))
    A(Flag(70, 320, "FROM D1"))
    # E5 feed/product exchanger, its charge-side bypass holding TT-3007
    P([(118, 320), (152, 320)], arrow_at=None)
    A(Equip(184, 320, 48, 38, "E5", "", kind="hx", ref="TT-3007"))
    P([(208, 320), (250, 320)], arrow_at=None)
    P([(152, 320), (152, 276), (168, 276)], arrow_at=None)
    A(ValveISA(180, 276, "TCV-3002", controller, show_pct=True))
    P([(192, 276), (232, 276), (232, 320)], arrow_at=None)
    A(MeasBox(184, 238, "TT-3007", "degC", 1))
    A(Label(184, 360, "vs reactor effluent", 6.5))
    P([(250, 320), (250, 340), (335, 340)], arrow_at=None)
    A(ValveISA(350, 340, "FCV-3005", controller, show_pct=True))
    P([(361, 340), (492, 340)], arrow_at=0.7)
    P([(250, 320), (250, 390), (335, 390)], arrow_at=None)
    A(ValveISA(350, 390, "FCV-3006", controller, show_pct=True))
    P([(361, 390), (473, 390)], arrow_at=0.7)
    P([(630, 340), (720, 340), (720, 365), (900, 365)], arrow_at=None)
    P([(650, 390), (720, 390), (720, 365)], arrow_at=None)
    P([(900, 365), (1102, 365)], arrow_at=0.5)
    A(Flag(1150, 365, "TO REACTOR"))
    A(LoopBox(830, 250, "TIC-3002", controller, "degC"))
    A(LoopBox(830, 470, "TIC-3003", controller, "degC"))
    A(LoopBox(990, 290, "TIC-3001", controller, "degC"))
    A(MeasBox(470, 180, "TT-3005", "degC", 0))
    A(Label(470, 152, "tube skin", 7))
    # combustion air on the damper
    A(Flag(80, 530, "COMB AIR"))
    P([(128, 530), (251, 530)], arrow_at=None)
    A(ValveISA(262, 530, "FCV-3004", controller, show_pct=True))
    P([(273, 530), (470, 530)], arrow_at=0.7)
    # fuel gas train
    A(Flag(80, 640, "FUEL GAS"))
    P([(128, 640), (207, 640)], arrow_at=None)
    A(MovISA(218, 640, "XV3001"))
    P([(229, 640), (311, 640)], arrow_at=None)
    A(ValveISA(322, 640, "FCV-3001", controller, show_pct=True))
    P([(333, 640), (520, 640), (520, 550)], arrow_at=0.6)
    # fuel oil train, split range
    A(Flag(80, 760, "FUEL OIL"))
    P([(128, 760), (207, 760)], arrow_at=None)
    A(MovISA(218, 760, "XV3002"))
    P([(229, 760), (311, 760)], arrow_at=None)
    A(ValveISA(322, 760, "FCV-3002", controller, show_pct=True))
    A(ValveISA(402, 760, "FCV-3003", controller, show_pct=True))
    P([(413, 760), (640, 760), (640, 550)], arrow_at=0.6)
    # stack through the induced draft fan
    P([(585, 290), (585, 150), (725, 150)], arrow_at=None)
    A(FanISA(740, 150, "ID-301", "XS-ID301-RUN"))
    P([(755, 150), (842, 150)], arrow_at=0.6)
    A(Flag(890, 150, "STACK"))
    A(LoopBox(1010, 110, "PIC-3002", controller, "mmH2O"))
    A(LoopBox(150, 700, "FIC-3001", controller, "Nm3/h"))
    A(LoopBox(290, 700, "FIC-3003", controller, "kNm3/h"))
    A(LoopBox(430, 700, "AIC-3001", controller, "mol%"))
    A(LoopBox(570, 700, "TIC-3004", controller, "degC"))
    A(LoopBox(710, 700, "PIC-3001", controller, "barg"))
    A(LoopBox(850, 700, "ZC-3001", controller, "%"))
    A(LoopBox(990, 700, "TIC-3005", controller, "degC"))
    A(MeasBox(240, 600, "AY-3001", "MJ/Nm3", 1))
    A(Label(240, 572, "inferred heating value", 6.5))
    return s


def build_u400_pid(controller) -> HmiScene:
    """R1 reactor and D3 separator: quench, depressuring, the effluent
    cooler and the three-phase separation."""
    s = HmiScene("R1 REACTOR / D3 SEPARATOR")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))
    A(Equip(300, 300, 90, 260, "R1", "REACTOR", name_at="left"))
    A(Flag(70, 150, "FROM H1"))
    P([(118, 150), (187, 150)], arrow_at=None)
    A(MovISA(198, 150, "XV4001"))
    P([(211, 150), (280, 150), (280, 170)], arrow_at=0.7)
    A(Flag(80, 250, "QUENCH GAS"))
    P([(128, 250), (191, 250)], arrow_at=None)
    A(ValveISA(202, 250, "FCV-4001", controller, show_pct=True))
    P([(213, 250), (255, 250)], arrow_at=0.7)
    # the additive injection, the reactor's quality handle
    A(Flag(80, 200, "ADDITIVE"))
    P([(128, 200), (191, 200)], arrow_at=None)
    A(ValveISA(202, 200, "FCV-4003", controller, show_pct=True))
    P([(213, 200), (246, 200)], arrow_at=0.8)
    P([(320, 170), (320, 110), (407, 110)], arrow_at=None)
    A(MovISA(418, 110, "BDV4001"))
    P([(429, 110), (482, 110)], arrow_at=0.8)
    A(Flag(530, 110, "FLARE"))
    A(MeasBox(420, 220, "TT-4002", "degC", 0))
    A(MeasBox(420, 262, "TT-4003", "degC", 0))
    A(MeasBox(420, 304, "AT-4001", "ppm", 0))
    A(MeasBox(420, 346, "XI-4001", "%", 1))
    A(MeasBox(560, 300, "FT-4005", "L/h", 0))
    A(LoopBox(150, 350, "FIC-4001", controller, "kNm3/h"))
    A(LoopBox(150, 430, "TIC-4001", controller, "degC"))
    A(LoopBox(150, 510, "TIC-4002", controller, "degC"))
    A(LoopBox(150, 590, "AIC-4001", controller, "ppm"))
    A(LoopBox(150, 670, "FIC-4002", controller, "L/h"))
    A(LoopBox(150, 750, "RC-4001", controller, "ratio"))
    P([(300, 430), (300, 500), (432, 500)], arrow_at=None)
    A(Equip(460, 500, 56, 44, "E-401", "", kind="hx", ref="TT-4005"))
    A(Flag(402, 400, "CWS", right=True))
    P([(438, 400), (460, 400), (460, 417)], arrow_at=None)
    A(ValveISA(460, 428, "FCV-4002", controller, vertical=True,
               show_pct=True))
    P([(460, 439), (460, 478)], arrow_at=None)
    A(LoopBox(580, 400, "TIC-4005", controller, "degC"))
    P([(488, 500), (610, 500)], arrow_at=0.6)
    A(Equip(700, 520, 180, 80, "D3", "", kind="tank",
            level_tag="LT-4001", name_at="left"))
    A(Label(700, 585, "SEPARATOR", 7.5))
    P([(660, 480), (660, 380), (767, 380)], arrow_at=None)
    A(MovISA(778, 380, "XV4002"))
    P([(789, 380), (869, 380)], arrow_at=None)
    A(ValveISA(880, 380, "PCV-4001", controller, show_pct=True))
    P([(891, 380), (952, 380)], arrow_at=0.8)
    A(Flag(1000, 380, "OFF-GAS"))
    A(LoopBox(1120, 380, "PIC-4001", controller, "barg"))
    P([(760, 560), (760, 650), (869, 650)], arrow_at=None)
    A(ValveISA(880, 650, "LCV-4001", controller, show_pct=True))
    P([(891, 650), (952, 650)], arrow_at=0.8)
    A(Flag(1000, 650, "TO T1"))
    A(LoopBox(1120, 650, "LIC-4001", controller, "%"))
    P([(680, 560), (680, 740), (789, 740)], arrow_at=None)
    A(ValveISA(800, 740, "LCV-4002", controller, show_pct=True))
    P([(811, 740), (877, 740)], arrow_at=0.8)
    A(Flag(925, 740, "SOUR WATER"))
    A(LoopBox(1120, 740, "LIC-4002", controller, "%"))
    return s


def build_u700_pid(controller) -> HmiScene:
    """B1 boiler: deaerator, feedwater train, drum, furnace with the
    cross-limited fuel and air, superheat, letdown and blowdown."""
    s = HmiScene("B1 BOILER")
    s.setSceneRect(QRectF(0, 0, 1400, 860))
    A, P = s.add, lambda pts, **k: s.add(Pipe(pts, **k))
    A(Equip(220, 180, 120, 60, "DA-701", "", kind="tank",
            level_tag="LT-7002"))
    A(Label(220, 228, "DEAERATOR", 7.5))
    A(Flag(80, 120, "CONDENSATE", right=True))
    P([(128, 120), (164, 120)], arrow_at=None)
    A(ValveISA(175, 120, "LCV-7001", controller, show_pct=True))
    P([(186, 120), (220, 120), (220, 150)], arrow_at=0.8)
    A(LoopBox(90, 300, "LIC-7002", controller, "%"))
    P([(220, 210), (220, 290), (292, 290)], arrow_at=None)
    A(MovISA(303, 290, "MOV7001A"))
    P([(314, 290), (362, 290)], arrow_at=None)
    A(PumpISA(375, 290, "XS-P701A-RUN", pair=("A", "B")))
    P([(388, 290), (580, 290)], arrow_at=0.8)
    A(ValveISA(470, 290, "FCV-7001", controller, show_pct=True))
    A(Equip(660, 290, 160, 70, "B1", "", kind="tank",
            level_tag="LT-7001", level_span=(-300, 300)))
    A(Label(660, 345, "STEAM DRUM", 7.5))
    A(LoopBox(350, 390, "FIC-7001", controller, "t/h"))
    A(LoopBox(350, 460, "LIC-7001", controller, "mm"))
    # steam to the header, desuperheated, with the letdown split
    P([(660, 255), (660, 150), (1112, 150)], arrow_at=0.9)
    A(ValveISA(800, 150, "TCV-7001", controller, show_pct=True))
    A(LoopBox(800, 80, "TIC-7001", controller, "degC"))
    A(Flag(1160, 150, "MP STEAM"))
    A(LoopBox(1010, 80, "PIC-7001", controller, "barg"))
    P([(950, 150), (950, 221)], arrow_at=None)
    A(ValveISA(950, 232, "PCV-7001", controller, vertical=True,
               show_pct=True))
    P([(950, 243), (950, 280), (1042, 280)], arrow_at=0.8)
    A(Flag(1090, 280, "LP HEADER"))
    A(LoopBox(1230, 280, "PIC-7002", controller, "barg"))
    # blowdown
    P([(740, 310), (837, 310)], arrow_at=None)
    A(ValveISA(848, 310, "FCV-7004", controller, show_pct=True))
    P([(859, 310), (922, 310)], arrow_at=0.8)
    A(Flag(970, 310, "BLOWDOWN"))
    A(LoopBox(1130, 365, "CIC-7001", controller, "uS/cm"))
    # the furnace, risers, cross-limited fuel and air
    A(Equip(660, 490, 150, 130, "", "FURNACE", kind="heater",
            name_dy=0.62))
    P([(630, 425), (630, 325)], arrow_at=None)
    P([(690, 425), (690, 325)], arrow_at=None)
    A(Flag(80, 555, "FUEL GAS"))
    P([(128, 555), (202, 555)], arrow_at=None)
    A(MovISA(213, 555, "XV7001"))
    P([(224, 555), (311, 555)], arrow_at=None)
    A(ValveISA(322, 555, "FCV-7002", controller, show_pct=True))
    P([(333, 555), (585, 555)], arrow_at=0.7)
    A(Flag(1120, 490, "AIR", right=False))
    P([(1078, 490), (999, 490)], arrow_at=None)
    A(ValveISA(988, 490, "FCV-7003", controller, show_pct=True))
    P([(977, 490), (920, 490)], arrow_at=None)
    A(FanISA(905, 490, "FD-701", "XS-FD701-RUN"))
    P([(890, 490), (735, 490)], arrow_at=0.7)
    A(LoopBox(150, 680, "FIC-7002", controller, "Nm3/h"))
    A(LoopBox(290, 680, "FIC-7003", controller, "kNm3/h"))
    A(LoopBox(430, 680, "AIC-7001", controller, "mol%"))
    A(LoopBox(570, 680, "AIC-7002", controller, "ppm"))
    # dual fuel: the oil trim under the total-duty master
    A(Flag(80, 610, "FUEL OIL"))
    P([(128, 610), (311, 610)], arrow_at=None)
    A(ValveISA(322, 610, "FCV-7005", controller, show_pct=True))
    P([(333, 610), (620, 610), (620, 557)], arrow_at=0.6)
    A(MeasBox(430, 585, "FT-7005", "kg/h", 0))
    A(LoopBox(710, 680, "FIC-7004", controller, "kg/h"))
    A(LoopBox(850, 680, "BIC-7001", controller, "MW"))
    # the OTHER boiler backstopping the shared header
    A(Flag(1160, 205, "STEAM EX B2", right=False))
    P([(1078, 205), (1040, 205), (1040, 150)], arrow_at=0.6)
    A(MeasBox(1300, 205, "FT-7006", "t/h", 1))
    return s


def build_overview(controller, alarms=None) -> HmiScene:
    """The whole-plant operating display in the reference's overview
    style: the converged routing left to right, inline loop faceplates,
    an alarm rail on the right and live trend tiles along the bottom."""
    s = HmiScene("PLANT OVERVIEW")
    s.setSceneRect(QRectF(0, 0, 1560, 900))
    A = s.add

    def P(pts, **k):
        k.setdefault("colour", NAVY)
        k.setdefault("width", 2.2)
        s.add(Pipe(pts, **k))

    def T(x, y, ref, cap, unit):
        A(TrendTile(x, y, ref, cap, unit, controller, mini=True))

    def V(x, y, tag, **kw):
        lim = {}
        if alarms is not None:
            lim = {p.atype: p.setpoint for p in alarms.points
                   if p.tag == tag and p.enabled
                   and p.atype in ("HI_HI", "HI", "LO", "LO_LO")}
        loop = next((l for l in controller.loops.values()
                     if l.pv_tag == tag), None)
        sp_fn = (lambda l=loop: l.pid.sp) if loop is not None else None
        A(VBar(x, y, tag, limits=lim, sp_fn=sp_fn, **kw))

    # ------------------------------------------------------ section headers
    for x0, x1, text in ((60, 470, "FEED & RECYCLE"),
                         (480, 810, "REACTION"),
                         (820, 1080, "PRIMARY SEPARATION"),
                         (1090, 1400, "PRODUCT FRACTIONATION"),
                         (1410, 1545, "UTILITIES")):
        A(Label((x0 + x1) / 2, 56, text, 8.5, bold=True, colour=NAVY))
        P([(x0, 56), ((x0 + x1) / 2 - 74, 56)], arrow_at=None, width=1.0)
        P([((x0 + x1) / 2 + 74, 56), (x1, 56)], arrow_at=None, width=1.0)

    # ---------------------------------------------------------- gas loop
    A(TextBox(170, 180, "H2 HEADER - EXTERNAL SOURCE", w=150, h=50,
              ref="PT-2004"))
    P([(246, 180), (364, 180)], arrow_at=0.85)
    A(Junction(400, 180, "COMPRESSOR SUCTION MIX", radius=36))
    A(Label(650, 164, "D3 RECYCLE GAS", 7.5, bold=True, colour=NAVY))
    P([(846, 180), (438, 180)], arrow_at=0.94)
    A(Junction(880, 180, "GAS SPLIT", radius=32))
    A(Label(1000, 164, "D3 SLIP GAS", 7.5, bold=True, colour=NAVY))
    P([(913, 180), (1092, 180)], arrow_at=0.85)
    A(Label(1130, 96, "NATURAL GAS", 8, bold=True, colour=NAVY))
    P([(1130, 106), (1130, 142)], arrow_at=0.8)
    A(Junction(1130, 180, "FUEL GAS MIX", radius=32))
    P([(1163, 180), (1236, 180)], arrow_at=0.8)
    A(Equip(1300, 180, 96, 60, "B1", "", kind="tank",
            ref="SCENE:B1 P&ID", name_dy=-0.25))
    P([(1349, 180), (1416, 180)], arrow_at=0.8)
    A(TextBox(1478, 180, "PLANT STEAM HEADER", w=112, h=50,
              ref="PIC-7001"))
    V(1240, 278, "LT-7001", unit="mm", span=(-300.0, 300.0))
    T(1370, 268, "PIC-7001", "MP STEAM", "barg")

    P([(400, 217), (400, 262)], arrow_at=0.7)
    A(Equip(400, 300, 92, 68, "K-C1", "", kind="comp",
            ref="SCENE:C1 P&ID", name_dy=0.0))
    A(Label(560, 282, "H2 QUENCH TO R-R1", 7, bold=True, colour=NAVY))
    P([(447, 300), (660, 300), (660, 462)], arrow_at=0.9)
    P([(400, 335), (400, 512)], arrow_at=0.7)
    T(255, 300, "PIC-2001", "C1 SUCTION", "barg")

    # ---------------------------------------------------------- feed train
    A(TextBox(66, 470, "FRESH FEED", w=88, h=44))
    P([(111, 470), (150, 470)], arrow_at=0.7)
    A(Equip(185, 505, 64, 150, "V-D1", "",
            ref="SCENE:D1 P&ID", name_dy=-0.30))
    V(255, 520, "LT-1001")
    P([(185, 581), (185, 660), (256, 660)], arrow_at=None)
    A(PumpISA(270, 660, "XS-P101A-RUN", pair=("A", "B")))
    A(Label(270, 690, "P-101 FEED PUMP", 7))
    P([(284, 660), (400, 660), (400, 578)], arrow_at=0.85)
    A(Junction(400, 545, "HEATER FEED MIX", radius=32))
    T(320, 410, "FIC-1001", "FEED RATE", "m3/h")

    P([(433, 545), (496, 545)], arrow_at=0.7)
    A(Equip(545, 545, 84, 96, "H-H1", "", kind="heater",
            ref="SCENE:H1 P&ID", name_dy=-0.36))
    T(545, 452, "TIC-3001", "HTR OUTLET", "degC")
    P([(588, 545), (656, 545)], arrow_at=0.7)
    A(Equip(690, 545, 62, 160, "R-R1", "", ref="SCENE:R1 P&ID"))
    T(800, 610, "TIC-4001", "RX OUTLET", "degC")
    A(Label(795, 302, "R-R1 OVERHEAD TO HPS", 7, bold=True, colour=NAVY))
    P([(690, 464), (690, 340), (872, 340)], arrow_at=0.9)
    P([(690, 626), (690, 772)], arrow_at=0.8)
    A(TextBox(690, 800, "EFFLUENT TREATMENT", w=120, h=48,
              ref="AIC-8001"))

    # --------------------------------------------------- primary separation
    A(Equip(910, 390, 72, 150, "V-D3", "",
            ref="SCENE:R1 P&ID", name_dy=-0.30))
    V(975, 425, "LT-4001")
    P([(910, 314), (910, 240), (880, 240), (880, 213)], arrow_at=0.9)
    P([(910, 466), (910, 540), (995, 540), (995, 548)], arrow_at=0.9)

    # ------------------------------------------------ product fractionation
    A(Equip(995, 650, 66, 200, "C-T1", "",
            ref="SCENE:T1 P&ID", name_dy=-0.30))
    V(925, 695, "LT-5002")
    P([(995, 548), (995, 505), (1102, 505)], arrow_at=None)
    A(Junction(1140, 505, "T1 DISTILLATE SPLIT", radius=34))
    P([(1140, 470), (1140, 440), (1236, 440)], arrow_at=0.85)
    A(TextBox(1300, 440, "TO STORAGE", w=96, h=42))
    A(Label(1235, 624, "T2 FEED", 7.5, bold=True, colour=NAVY))
    P([(1140, 540), (1140, 640), (1322, 640)], arrow_at=0.92)
    A(Equip(1355, 690, 60, 190, "C-T2", "",
            ref="SCENE:T2 P&ID", name_dy=-0.30))
    V(1292, 735, "LT-6002")
    A(Label(1416, 542, "OVERHEAD VAPOR TO FLARE", 6.5, colour=NAVY))
    P([(1355, 594), (1355, 560), (1444, 560)], arrow_at=0.85)
    A(TextBox(1506, 560, "FLARE", w=84, h=38))
    P([(1386, 660), (1442, 660)], arrow_at=0.8)
    A(TextBox(1506, 660, "C3 DISTILLATE PRODUCT", w=100, h=48))
    P([(1355, 786), (1355, 812), (1444, 812)], arrow_at=0.85)
    A(TextBox(1506, 812, "C4 BOTTOMS PRODUCT", w=100, h=48))
    T(823, 430, "AT-5001", "T1 QUALITY", "mol%")
    T(1195, 764, "AT-6001", "R2 QUALITY", "mol%")

    # the recycle spine, back to the feed drum; the MOV sits on the
    # horizontal run, where its flow direction reads correctly
    P([(995, 750), (995, 852), (313, 852)], arrow_at=None)
    A(MovISA(300, 852, "MOV1002"))
    P([(287, 852), (120, 852), (120, 540), (150, 540)], arrow_at=0.95)
    A(Label(620, 838, "C-T1 BOTTOMS RECYCLE TO V-D1", 8, bold=True,
            colour=NAVY))

    A(EsdBanner(1420, 878, controller))
    return s


# ------------------------------------------------------------------- widget
class _FitView(QGraphicsView):
    """Refits on its OWN resize.

    A container's resizeEvent runs before the layout has resized its
    children, so fitting from there uses the view's stale size and the
    graphic settles at the wrong scale after a single resize pass.
    """

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        if self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)


class OperatorDisplay(QWidget):
    """Title band, the graphic, and the display navigation buttons."""

    def __init__(self, db, controller, open_loop_fp, open_tag_fp,
                 context_provider=None, alarms=None,
                 standalone: bool = False) -> None:
        super().__init__()
        self.standalone = standalone
        self.db = db
        self.controller = controller
        self.alarms = alarms
        self._open_loop_fp = open_loop_fp
        self._open_tag_fp = open_tag_fp
        self._context_provider = context_provider

        self.header = QLabel("PLANT OVERVIEW")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setFixedHeight(30)
        self.header.setFont(theme.font(13, bold=True))
        self.header.setStyleSheet(
            f"background: {BAND.name()}; color: {TEXT.name()};"
            "border-bottom: 1px solid #C2C6CB;")

        self.view = _FitView()
        self.view.setRenderHint(QPainter.Antialiasing, True)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)

        self.scenes: Dict[str, HmiScene] = {}
        self._install("Overview", build_overview(controller, alarms))
        if controller is not None:
            for name, units in (
                    ("Feed & Fuel", [("U010", "Fuel gas header"),
                                     ("U100", "Feed section")]),
                    ("Reaction", [("U200", "Recycle compressor"),
                                  ("U300", "Charge heater"),
                                  ("U400", "Reactor and separator")]),
                    ("Fractionation", [("U500", "Column T1"),
                                       ("U600", "Column T2")]),
                    ("Utilities", [("U700", "Boiler and steam"),
                                   ("U800", "Effluent treatment")])):
                self._install(name, build_section(name, units, controller,
                                                  self.db))
            self._install("D1 P&ID", build_u100_pid(controller))
            self._install("C1 P&ID", build_u200_pid(controller))
            self._install("H1 P&ID", build_u300_pid(controller))
            self._install("R1 P&ID", build_u400_pid(controller))
            self._install("T1 P&ID", build_column_pid(5, controller))
            self._install("T2 P&ID", build_column_pid(6, controller))
            self._install("B1 P&ID", build_u700_pid(controller))

        # in the main window, navigation lives in the nav band; a
        # standalone (second-monitor) display carries its own picker
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if standalone:
            from PySide6.QtWidgets import QComboBox
            row = QHBoxLayout()
            row.setContentsMargins(8, 4, 8, 4)
            self.picker = QComboBox()
            self.picker.addItems(list(self.scenes))
            self.picker.currentTextChanged.connect(self.show_scene)
            row.addWidget(QLabel("Display"))
            row.addWidget(self.picker)
            row.addStretch(1)
            lay.addLayout(row)
        lay.addWidget(self.header)
        lay.addWidget(self.view, 1)
        self.show_scene("Overview")

    def _install(self, name: str, scene: HmiScene) -> None:
        scene.on_open = self._open
        scene.context_provider = self._context_provider
        wire_scene_signals(scene, self.controller)
        self.scenes[name] = scene

    def _open(self, ref: str) -> None:
        if ref.startswith("SCENE:"):       # HP-HMI click-through
            name = ref[6:]
            if name in self.scenes:
                self.show_scene(name)
            return
        if self.controller is not None and (
                ref in self.controller.loops
                or self.controller.loop_for_tag(ref) is not None):
            self._open_loop_fp(ref)
        elif ref in self.db:
            self._open_tag_fp(ref)

    def show_scene(self, name: str) -> None:
        scene = self.scenes[name]
        self.view.setScene(scene)
        self.header.setText(scene.title)
        self.view.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)
        self.current_scene = name
        cb = getattr(self, "on_scene_changed", None)
        if cb is not None:
            cb(name)

    def refresh(self, snap: Snapshot) -> None:
        scene = self.view.scene()
        if isinstance(scene, HmiScene):
            scene.refresh(snap)
