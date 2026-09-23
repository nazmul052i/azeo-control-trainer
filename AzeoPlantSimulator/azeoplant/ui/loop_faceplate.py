"""PID loop faceplate and detail display, with the shared Azeo controls and theme.

``LoopFaceplate`` is the Loop_fp of ``docs/FB_FP/PID_FP.png``, on the same
pale blue panel: three header lines, PV in steel blue over its tag, OUT in
teal over the valve tag with the Simulate badge, the mini-faceplate chevron
and ADAPT state over the target and actual modes, SP and OUT slews as flat
triangles, the PV bar with its scale, limit ticks, working-SP marker and the
white SP slider, the Bypass/Normal toggle, the OUT bar with its pointer, the
trend chart, the alarm list and the icon button row.

``LoopDetail`` is the Loop_dt of ``docs/FB_FP/PID_details.png``: the limits
column through ARW and SP limits, the Simulate group (live: the block really
runs on the simulate value), the tuning group with the ADAPT column, filters,
SP rates, structure with Beta and Gamma, the per-alarm priority, Enab, OOS
and Shlv columns with help buttons, and the tabbed diagnostics.

Nothing here uses a spin box; every numeric is a flat field, per the images.
"""

from __future__ import annotations

import math
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath,
                           QPen, QPolygonF)
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox,
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy,
                               QTabWidget, QVBoxLayout, QWidget)

from ..control.pid import Mode, Structure
from .live_dialog import LiveDialog, retain_detail

# ------------------------------------------------------------ Azeo palette
PANEL = "#DEE3EA"
PANEL_DK = "#D2D9E2"
FRAME = "#A9B2BC"
TXT = "#333A42"
DIM = "#6C7681"
PV_BLUE = "#2E5F9E"
OUT_TEAL = "#0E6B5C"
WHITE = "#FFFFFF"
ALARM_RED = "#C1272D"
ADVISORY = "#3B6FA8"
BAR_TROUGH = "#CBD3DC"
BAR_FILL = "#5B7FA6"
LIMIT_BAND = "#E8DFC9"

STYLE = f"""
QDialog {{ background: {PANEL}; }}
QLabel {{ color: {TXT}; background: transparent; }}
QLineEdit {{
    background: {WHITE}; border: 1px solid {FRAME}; border-radius: 0px;
    padding: 1px 3px; color: {TXT}; selection-background-color: {PV_BLUE};
}}
QComboBox {{
    background: {PANEL}; border: 1px solid transparent; color: {TXT};
    padding: 0px 2px;
}}
QComboBox:hover {{ border: 1px solid {FRAME}; background: {WHITE}; }}
QComboBox::drop-down {{ border: none; width: 14px; }}
QCheckBox {{ background: transparent; }}
QTabWidget::pane {{ border: 1px solid {FRAME}; background: {WHITE}; }}
QTabBar::tab {{
    background: {PANEL_DK}; border: 1px solid {FRAME}; padding: 2px 8px;
    font-size: 8pt; color: {TXT};
}}
QTabBar::tab:selected {{ background: {WHITE}; }}
QPushButton {{
    background: {PANEL_DK}; border: 1px solid {FRAME}; padding: 2px 10px;
    color: {TXT};
}}
QPushButton:hover {{ background: {WHITE}; }}
"""


def _f(size: float, bold: bool = False) -> QFont:
    f = QFont("Arial", 1)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def _lbl(text: str, size: float = 8.0, bold: bool = False,
         colour: str = TXT, align=Qt.AlignLeft) -> QLabel:
    l = QLabel(text)
    l.setFont(_f(size, bold))
    l.setStyleSheet(f"color: {colour}; background: transparent;")
    l.setAlignment(align)
    return l


class Slew(QAbstractButton):
    """The flat triangle slew button: outline for SP, filled teal for OUT."""

    def __init__(self, up: bool, filled: bool, cb) -> None:
        super().__init__()
        self.up, self.filled = up, filled
        self.setFixedSize(34, 15)
        self.setAutoRepeat(True)
        self.setAutoRepeatInterval(120)
        self.clicked.connect(cb)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pts = ([QPointF(4, h - 3), QPointF(w - 4, h - 3), QPointF(w / 2, 2)]
               if self.up else
               [QPointF(4, 3), QPointF(w - 4, 3), QPointF(w / 2, h - 2)])
        colour = QColor(OUT_TEAL) if self.filled else QColor("#8C97A3")
        if self.isDown():
            colour = colour.darker(130)
        p.setPen(QPen(colour, 1.4))
        p.setBrush(QBrush(colour) if self.filled else Qt.NoBrush)
        p.drawPolygon(QPolygonF(pts))
        p.end()


class ValueField(QLineEdit):
    """An editable operator value: rendered as plain text until the operator engages it."""

    def __init__(self, colour: str, size: float = 10.5, width: int = 66) -> None:
        super().__init__()
        self.setFixedWidth(width)
        self.setFont(_f(size, True))
        self.setAlignment(Qt.AlignRight)
        self.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: 1px solid "
            f"transparent; color: {colour}; }}"
            f"QLineEdit:hover, QLineEdit:focus {{ background: {WHITE}; "
            f"border: 1px solid {FRAME}; }}")


class PVBar(QWidget):
    """The PV bar per the reference's own anatomy drawing: a wide pale scale
    with major and minor divisions, and a NARROW dark PV strip hugging its
    right edge, carrying the alarm limit ticks and the working-SP notch. The
    white SP slider rides the left edge at the setpoint height."""

    def __init__(self, loop) -> None:
        super().__init__()
        self.loop = loop
        self.setFixedSize(50, 196)

    def paintEvent(self, ev) -> None:  # noqa: N802
        pid = self.loop.pid
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        scale = QRectF(16, 14, 19, h - 28)          # the wide pale PV scale
        bar = QRectF(scale.right(), 14, 7, h - 28)  # the narrow PV strip

        def y_of(eu: float) -> float:
            f = (eu - pid.pv_eu0) / pid.pv_span
            return scale.bottom() - max(0.0, min(1.0, f)) * scale.height()

        # scale with major and minor divisions
        p.fillRect(scale, QColor(BAR_TROUGH))
        p.setPen(QPen(QColor("#AEB7C0"), 1))
        for i in range(21):
            y = scale.top() + scale.height() * i / 20.0
            major = i % 5 == 0
            p.drawLine(QPointF(scale.right() - (9 if major else 5), y),
                       QPointF(scale.right() - 1, y))
        p.setPen(QPen(QColor(FRAME), 1))
        p.drawRect(scale)

        # the PV strip: pale background, dark fill up to the PV
        p.fillRect(bar, QColor("#D6DDE5"))
        y_pv = y_of(pid.pv)
        p.fillRect(QRectF(bar.left(), y_pv, bar.width(), bar.bottom() - y_pv),
                   QColor("#41618C"))
        p.setPen(QPen(QColor(FRAME), 1))
        p.drawRect(bar)

        # Alarm limit marks on the strip. Per the reference and ISA-101 they
        # are NEUTRAL dark marks; a mark takes its alarm colour only while
        # that alarm is actually active, so colour on this bar always means
        # something is wrong right now.
        a = pid.alarms
        for lim, active, colour in (
                (pid.hi_hi_lim, a.hi_hi, ALARM_RED),
                (pid.hi_lim, a.hi, "#E8A317"),
                (pid.lo_lim, a.lo, "#E8A317"),
                (pid.lo_lo_lim, a.lo_lo, ALARM_RED)):
            if math.isfinite(lim) and pid.pv_eu0 <= lim <= pid.pv_eu100:
                y = y_of(lim)
                p.setPen(QPen(QColor(colour if active else "#3D4F5D"), 2))
                p.drawLine(QPointF(bar.left() - 1, y),
                           QPointF(bar.right() + 1, y))

        # working SP: the small white notch on the strip
        y_sp = y_of(pid.sp_wrk)
        p.setPen(QPen(QColor("#5A626B"), 0.8))
        p.setBrush(QBrush(QColor(WHITE)))
        p.drawRect(QRectF(bar.left() - 1, y_sp - 1.6, bar.width() + 2, 3.2))

        # the SP slider, the white tab at the scale's left edge
        tri = QPolygonF([QPointF(scale.left() - 1, y_sp),
                         QPointF(scale.left() - 11, y_sp - 6),
                         QPointF(scale.left() - 11, y_sp + 6)])
        p.setPen(QPen(QColor("#8C97A3"), 1))
        p.setBrush(QBrush(QColor(WHITE)))
        p.drawPolygon(tri)

        # EU labels, over and under the strip
        p.setPen(QPen(QColor(DIM)))
        p.setFont(_f(7))
        p.drawText(QRectF(0, 0, w, 12), Qt.AlignHCenter, f"{pid.pv_eu100:g}")
        p.drawText(QRectF(0, h - 12, w, 12), Qt.AlignHCenter,
                   f"{pid.pv_eu0:g}")
        p.end()


class OutBar(QWidget):
    """The OUT bar graph with its scale and the teal pointer beneath."""

    def __init__(self, loop) -> None:
        super().__init__()
        self.loop = loop
        self.setFixedHeight(34)

    def paintEvent(self, ev) -> None:  # noqa: N802
        pid = self.loop.pid
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        bar = QRectF(10, 12, w - 20, 10)
        p.setPen(QPen(QColor(DIM)))
        p.setFont(_f(7))
        p.drawText(QRectF(6, 0, 60, 11), Qt.AlignLeft,
                   f"{pid.out_lo_lim:g}")
        p.drawText(QRectF(w - 66, 0, 60, 11), Qt.AlignRight,
                   f"{pid.out_hi_lim:g}")
        p.fillRect(bar, QColor(LIMIT_BAND))
        x = bar.left() + bar.width() * max(0.0, min(1.0, pid.out / 100.0))
        p.fillRect(QRectF(bar.left(), bar.top(), x - bar.left(), bar.height()),
                   QColor(OUT_TEAL))
        p.setPen(QPen(QColor(FRAME), 1))
        p.drawRect(bar)
        for i in range(11):
            xx = bar.left() + bar.width() * i / 10.0
            p.drawLine(QPointF(xx, bar.bottom()),
                       QPointF(xx, bar.bottom() + (4 if i % 5 == 0 else 2)))
        ptr = QPolygonF([QPointF(x, bar.bottom() + 2),
                         QPointF(x - 4, bar.bottom() + 9),
                         QPointF(x + 4, bar.bottom() + 9)])
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(OUT_TEAL)))
        p.drawPolygon(ptr)
        p.end()


class Trend(QWidget):
    """The faceplate trend chart, white field with the EU scale at left."""

    def __init__(self, loop) -> None:
        super().__init__()
        self.loop = loop
        self.setFixedHeight(64)
        self._hist: List[tuple] = []

    def sample(self) -> None:
        pid = self.loop.pid
        self._hist.append((pid.pv, pid.sp_wrk, pid.out))
        if len(self._hist) > 300:
            self._hist = self._hist[-300:]

    def paintEvent(self, ev) -> None:  # noqa: N802
        pid = self.loop.pid
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(26, 3, self.width() - 32, self.height() - 8)
        p.setPen(QPen(QColor(DIM)))
        p.setFont(_f(7))
        p.drawText(QRectF(0, 0, 24, 11), Qt.AlignRight, f"{pid.pv_eu100:g}")
        p.drawText(QRectF(0, self.height() - 13, 24, 11), Qt.AlignRight,
                   f"{pid.pv_eu0:g}")
        p.fillRect(r, QColor("#EDEFF2"))
        p.setPen(QPen(QColor(FRAME), 1))
        p.drawRect(r)
        if len(self._hist) >= 2:
            for idx, colour, lo, span in (
                    (2, QColor(OUT_TEAL), 0.0, 100.0),
                    (1, QColor("#8C97A3"), pid.pv_eu0, pid.pv_span),
                    (0, QColor(PV_BLUE), pid.pv_eu0, pid.pv_span)):
                p.setPen(QPen(colour, 1.2))
                n = len(self._hist)
                last = None
                for i, row in enumerate(self._hist):
                    x = r.left() + r.width() * i / max(n - 1, 1)
                    f = (row[idx] - lo) / span
                    y = r.bottom() - max(0.0, min(1.0, f)) * r.height()
                    if last is not None:
                        p.drawLine(last, QPointF(x, y))
                    last = QPointF(x, y)
        p.end()


class Toggle(QWidget):
    """The Bypass / Normal vertical toggle, drawn per the reference."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(64, 44)
        self.bypass = False
        self.setToolTip("Bypass is not used by this strategy")

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pill = QRectF(4, 6, 14, 32)
        p.setPen(QPen(QColor(FRAME), 1))
        p.setBrush(QBrush(QColor("#C6CDD6")))
        p.drawRoundedRect(pill, 7, 7)
        knob_y = 8 if self.bypass else 22
        p.setBrush(QBrush(QColor(WHITE)))
        p.drawEllipse(QRectF(5, knob_y, 12, 12))
        p.setFont(_f(7.5, True))
        p.setPen(QPen(QColor(TXT if self.bypass else DIM)))
        p.drawText(QRectF(22, 4, 42, 14), Qt.AlignLeft, "Bypass")
        p.setPen(QPen(QColor(DIM if self.bypass else TXT)))
        p.drawText(QRectF(22, 24, 42, 14), Qt.AlignLeft, "Normal")
        p.end()


class IconButton(QAbstractButton):
    """The round icon buttons along the faceplate's bottom edge."""

    def __init__(self, glyph: str, tip: str, cb=None, enabled=True) -> None:
        super().__init__()
        self.glyph = glyph
        self.setFixedSize(30, 30)
        self.setToolTip(tip)
        self.setEnabled(enabled and cb is not None)
        if cb:
            self.clicked.connect(cb)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(2, 2, 26, 26)
        on = self.isEnabled()
        p.setPen(QPen(QColor(FRAME), 1.2))
        p.setBrush(QBrush(QColor(WHITE)))
        p.drawEllipse(r)
        blue = QColor(ADVISORY if on else "#AEB7C0")
        c = r.center()
        g = self.glyph
        if g == "detail":                    # display page with a lens
            p.setPen(QPen(blue, 1.4))
            p.drawRect(QRectF(c.x() - 8, c.y() - 8, 10, 12))
            p.drawLine(QPointF(c.x() - 6, c.y() - 4), QPointF(c.x(), c.y() - 4))
            p.drawLine(QPointF(c.x() - 6, c.y() - 1), QPointF(c.x(), c.y() - 1))
            p.drawEllipse(QRectF(c.x(), c.y(), 7, 7))
            p.drawLine(QPointF(c.x() + 6, c.y() + 6), QPointF(c.x() + 9, c.y() + 9))
        elif g == "trend":                   # magnifier over a wiggle
            p.setPen(QPen(blue, 1.6))
            p.drawEllipse(QRectF(c.x() - 7, c.y() - 7, 11, 11))
            p.drawLine(QPointF(c.x() + 3, c.y() + 3), QPointF(c.x() + 8, c.y() + 8))
            p.setPen(QPen(blue, 1.1))
            p.drawLine(QPointF(c.x() - 5, c.y()), QPointF(c.x() - 2, c.y() - 3))
            p.drawLine(QPointF(c.x() - 2, c.y() - 3), QPointF(c.x() + 1, c.y() + 1))
        elif g == "group":                   # the twin faceplate bars
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#C1272D") if on else blue))
            p.drawRect(QRectF(c.x() - 7, c.y() - 5, 4, 12))
            p.setBrush(QBrush(blue))
            p.drawRect(QRectF(c.x() - 2, c.y() - 2, 4, 9))
            p.setBrush(QBrush(QColor(OUT_TEAL) if on else blue))
            p.drawRect(QRectF(c.x() + 3, c.y() - 7, 4, 14))
        elif g == "sliders":                 # the little slider stack
            p.setPen(QPen(blue, 1.3))
            for i, dx in enumerate((-3, 2, -1)):
                y = c.y() - 5 + i * 5
                p.drawLine(QPointF(c.x() - 8, y), QPointF(c.x() + 8, y))
                p.fillRect(QRectF(c.x() + dx - 1.5, y - 2.5, 3, 5),
                           QBrush(blue))
        elif g == "list":                    # the parameter list
            p.setPen(QPen(blue, 1.5))
            for i in range(4):
                y = c.y() - 6 + i * 4
                p.drawLine(QPointF(c.x() - 7, y), QPointF(c.x() + 7, y))
        elif g == "bell":                    # the alarm bell, gold
            gold = QColor("#E8A317") if on else blue
            p.setPen(QPen(gold.darker(115), 1.2))
            p.setBrush(QBrush(gold))
            path = QPainterPath()
            path.moveTo(c.x() - 7, c.y() + 4)
            path.quadTo(c.x() - 7, c.y() - 8, c.x(), c.y() - 8)
            path.quadTo(c.x() + 7, c.y() - 8, c.x() + 7, c.y() + 4)
            path.closeSubpath()
            p.drawPath(path)
            p.drawEllipse(QRectF(c.x() - 2, c.y() + 5, 4, 4))
        p.end()


class Chevron(QAbstractButton):
    """The mini-faceplate button: a small drawn chevron, not a text glyph."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(22, 12)
        self.setToolTip("Mini faceplate (not used)")
        self.setEnabled(False)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor("#8C97A3"), 1.6))
        p.drawLine(QPointF(6, 9), QPointF(11, 4))
        p.drawLine(QPointF(11, 4), QPointF(16, 9))
        p.end()


class SimBadge(QWidget):
    """The Simulate Active icon: the grey circle with the italic serif S,
    visible only while simulation is active, per the reference table."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(16, 16)
        self.active = False

    def paintEvent(self, ev) -> None:  # noqa: N802
        if not self.active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor("#888888"), 1))
        p.setBrush(QBrush(QColor("#DDDDDD")))
        p.drawEllipse(QRectF(1, 1, 14, 14))
        f = QFont("Georgia", 1)
        f.setPointSizeF(8.5)
        f.setItalic(True)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor("#555555")))
        p.drawText(QRectF(0, 0, 16, 15), Qt.AlignCenter, "S")
        p.end()


class AlarmIcon(QLabel):
    """The alarm priority symbol of the reference table: the red circle with
    the white cross for CRITICAL, the yellow triangle for WARNING, the purple
    diamond for ADVISORY, and the grey bell when the alarm is suppressed.
    Painted only while an alarm is active."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(18, 18)
        self.active = False
        self.priority = "CRITICAL"
        self.suppressed = False

    def paintEvent(self, ev) -> None:  # noqa: N802
        if not self.active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.suppressed:                       # the grey bell
            grey = QColor("#8C97A3")
            p.setPen(QPen(grey.darker(115), 1))
            p.setBrush(QBrush(grey))
            path = QPainterPath()
            path.moveTo(3, 13)
            path.quadTo(3, 2, 9, 2)
            path.quadTo(15, 2, 15, 13)
            path.closeSubpath()
            p.drawPath(path)
            p.drawEllipse(QRectF(7.5, 14, 3, 3))
        elif self.priority == "WARNING":          # the yellow triangle
            p.setPen(QPen(QColor("#B27C0E"), 1))
            p.setBrush(QBrush(QColor("#E8A317")))
            p.drawPolygon(QPolygonF([QPointF(9, 2), QPointF(16.5, 15.5),
                                     QPointF(1.5, 15.5)]))
            p.setPen(QPen(QColor("#333A42"), 2))
            p.drawLine(QPointF(9, 7), QPointF(9, 11.5))
            p.drawPoint(QPointF(9, 14))
        elif self.priority == "ADVISORY":         # the purple diamond
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#8E44AD")))
            p.drawPolygon(QPolygonF([QPointF(9, 1), QPointF(17, 9),
                                     QPointF(9, 17), QPointF(1, 9)]))
            p.setPen(QPen(QColor(WHITE), 2))
            p.drawLine(QPointF(9, 5), QPointF(9, 10.5))
            p.drawPoint(QPointF(9, 13.5))
        else:                                     # CRITICAL: circle and cross
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(ALARM_RED)))
            p.drawEllipse(QRectF(1, 1, 16, 16))
            p.setPen(QPen(QColor(WHITE), 2.4))
            p.drawLine(QPointF(6, 6), QPointF(12, 12))
            p.drawLine(QPointF(12, 6), QPointF(6, 12))
        p.end()


# ============================================================== the faceplate
class LoopFaceplate(LiveDialog):
    def __init__(self, loop, controller, unit_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.loop, self.controller = loop, controller
        pid = loop.pid
        self.setWindowTitle(f"{loop.module}  faceplate")
        self.setFixedWidth(292)
        self.setStyleSheet(STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(2)

        # ------------------------------------------------------------ header
        root.addWidget(_lbl(loop.description, 7.5, colour=DIM,
                            align=Qt.AlignHCenter))
        root.addWidget(_lbl(loop.module, 13, True, align=Qt.AlignHCenter))
        root.addWidget(_lbl(self.loop.pv_tag + "  ·  " + unit_name, 7.5,
                            colour=DIM, align=Qt.AlignHCenter))

        # -------------------------------------------------- PV / OUT values
        vals = QGridLayout()
        vals.setContentsMargins(2, 4, 2, 0)
        self.lbl_pv = _lbl("-", 14, True, PV_BLUE)
        self.lbl_out = _lbl("-", 14, True, OUT_TEAL, Qt.AlignRight)
        vals.addWidget(self.lbl_pv, 0, 0)
        vals.addWidget(self.lbl_out, 0, 2)
        vals.addWidget(_lbl(loop.pv_tag, 7, colour=DIM), 1, 0)
        out_cap = QHBoxLayout()
        out_cap.addStretch(1)
        out_cap.addWidget(_lbl(loop.out_tag or "cascade", 7, colour=DIM))
        self.sim_badge = SimBadge()
        out_cap.addWidget(self.sim_badge)
        vals.addLayout(out_cap, 1, 2)
        root.addLayout(vals)

        # ----------------------------- middle: slews | bar+toggle | modes
        mid = QGridLayout()
        mid.setContentsMargins(0, 2, 0, 0)
        mid.setHorizontalSpacing(4)

        left = QVBoxLayout()
        left.addStretch(1)
        left.addWidget(Slew(True, False, lambda: self._nudge_sp(+1)),
                       alignment=Qt.AlignHCenter)
        self.ed_sp = ValueField(TXT, 10.5)
        self.ed_sp.returnPressed.connect(self._sp_entered)
        left.addWidget(self.ed_sp, alignment=Qt.AlignHCenter)
        left.addWidget(Slew(False, False, lambda: self._nudge_sp(-1)),
                       alignment=Qt.AlignHCenter)
        left.addSpacing(14)
        left.addWidget(Slew(True, True, lambda: self._nudge_out(+1)),
                       alignment=Qt.AlignHCenter)
        self.ed_out = ValueField(OUT_TEAL, 10.5)
        self.ed_out.returnPressed.connect(self._out_entered)
        left.addWidget(self.ed_out, alignment=Qt.AlignHCenter)
        left.addWidget(Slew(False, True, lambda: self._nudge_out(-1)),
                       alignment=Qt.AlignHCenter)
        left.addStretch(2)
        mid.addLayout(left, 0, 0)

        self.bar = PVBar(loop)
        mid.addWidget(self.bar, 0, 1, alignment=Qt.AlignTop)

        def bang_row(widget) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(3)
            mark = _lbl("!", 7.5, True, WHITE, Qt.AlignCenter)
            mark.setFixedSize(10, 12)
            mark.setStyleSheet(
                f"background: {ADVISORY}; color: {WHITE}; border-radius: 2px;")
            row.addWidget(mark, 0, Qt.AlignVCenter)
            row.addWidget(widget, 1)
            return row

        right = QVBoxLayout()
        right.setSpacing(1)
        chev = Chevron()
        right.addWidget(chev, 0, Qt.AlignLeft)
        right.addLayout(bang_row(_lbl("ADAPT", 8, True, "#9AA6B0")))
        self.cmb_mode = QComboBox()
        self.cmb_mode.setFont(_f(9, True))
        allowed = [Mode.MAN, Mode.AUTO] + ([Mode.CAS] if loop.master else [])
        for m in allowed:
            self.cmb_mode.addItem(m.value)
        self.cmb_mode.activated.connect(self._mode_selected)
        right.addLayout(bang_row(self.cmb_mode))
        self.lbl_actual = _lbl("-", 11, True)
        right.addLayout(bang_row(self.lbl_actual))
        right.addSpacing(8)
        self.toggle = Toggle()
        right.addWidget(self.toggle)
        right.addStretch(1)
        mid.addLayout(right, 0, 2)
        root.addLayout(mid)

        self.out_bar = OutBar(loop)
        root.addWidget(self.out_bar)
        self.trend = Trend(loop)
        root.addWidget(self.trend)

        # -------------------------------------------------------- alarm list
        alarm_hdr = QHBoxLayout()
        alarm_hdr.setContentsMargins(2, 2, 2, 0)
        alarm_hdr.addWidget(_lbl("Ack", 7.5, colour=DIM))
        alarm_hdr.addWidget(_lbl("Param", 7.5, colour=DIM), 1)
        alarm_hdr.addWidget(_lbl("Help", 7.5, colour=DIM))
        root.addLayout(alarm_hdr)
        self.alarm_area = QLabel()
        self.alarm_area.setFixedHeight(46)
        self.alarm_area.setStyleSheet(
            f"background: {WHITE}; border: 1px solid {FRAME};")
        self.alarm_area.setFont(_f(8))
        self.alarm_area.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.alarm_area.setMargin(3)
        alarm_wrap = QHBoxLayout()
        alarm_wrap.setSpacing(0)
        alarm_wrap.addWidget(self.alarm_area, 1)
        rail = QVBoxLayout()
        rail.setSpacing(0)
        for arrow in ("▲", "▼"):
            a = _lbl(arrow, 6, colour=DIM, align=Qt.AlignCenter)
            a.setFixedSize(13, 23)
            a.setStyleSheet(f"background: {PANEL_DK}; color: {DIM};"
                            f"border: 1px solid {FRAME};")
            rail.addWidget(a)
        alarm_wrap.addLayout(rail)
        root.addLayout(alarm_wrap)

        unit_row = QHBoxLayout()
        unit_row.addWidget(_lbl("Unit:", 7.5, colour=DIM))
        unit_row.addWidget(_lbl(unit_name, 8, True))
        unit_row.addStretch(1)
        root.addLayout(unit_row)

        # -------------------------------------------------- icon button row
        icons = QHBoxLayout()
        icons.setSpacing(6)
        icons.addWidget(IconButton("detail", "Loop detail display",
                                   self._open_detail))
        icons.addWidget(IconButton("trend", "Trend", None, False))
        icons.addWidget(IconButton("group", "Faceplate group", None, False))
        icons.addWidget(IconButton("list", "Module explorer", None, False))
        icons.addWidget(IconButton("sliders", "Tune", None, False))
        icons.addWidget(IconButton("bell", "Acknowledge alarms",
                                   self._ack_alarms))
        icons.addStretch(1)
        root.addLayout(icons)

        self._detail: Optional[LoopDetail] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)
        self._refresh()

    # ------------------------------------------------------------ operations
    def _nudge_sp(self, sign: int) -> None:
        pid = self.loop.pid
        pid.sp = pid.sp + sign * pid.pv_span * 0.005

    def _nudge_out(self, sign: int) -> None:
        pid = self.loop.pid
        if pid.actual_mode in (Mode.MAN, Mode.ROUT):
            pid.out = max(0.0, min(100.0, pid.out + sign * 0.5))

    def _sp_entered(self) -> None:
        try:
            self.loop.pid.sp = float(self.ed_sp.text())
            self.loop.pid.note_sp_change()
        except ValueError:
            pass

    def _out_entered(self) -> None:
        try:
            v = float(self.ed_out.text())
        except ValueError:
            return
        pid = self.loop.pid
        if pid.actual_mode in (Mode.MAN, Mode.ROUT):
            pid.out = max(0.0, min(100.0, v))

    def _mode_selected(self) -> None:
        self.loop.pid.set_mode(Mode(self.cmb_mode.currentText()))

    def _ack_alarms(self) -> None:
        self.loop.pid.alarm_shelved = {}
        alarms = getattr(self.parent(), "alarms", None)
        if alarms is not None:
            alarms.ack_tag(self.loop.pv_tag)

    def _open_detail(self) -> None:
        if self._detail is None:
            retain_detail(self, LoopDetail(self.loop, self))
        else:
            self._detail.raise_()
        self._detail.show()

    # --------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        pid = self.loop.pid
        self.lbl_pv.setText(f"{pid.pv:.2f}")
        self.lbl_out.setText(f"{pid.out:.1f} %")
        self.lbl_actual.setText(pid.actual_mode.value + ' ▾')
        shed = pid.actual_mode != pid.target_mode
        self.lbl_actual.setStyleSheet(
            f"color: {'#E8A317' if shed else TXT}; background: transparent;")
        self.sim_badge.active = pid.simulate_enable
        self.sim_badge.update()
        if not self.ed_sp.hasFocus():
            self.ed_sp.setText(f"{pid.sp_wrk:.2f}")
        if not self.ed_out.hasFocus():
            self.ed_out.setText(f"{pid.out:.1f}")
        self.ed_out.setEnabled(pid.actual_mode in (Mode.MAN, Mode.ROUT))
        idx = self.cmb_mode.findText(pid.target_mode.value)
        if idx >= 0 and not self.cmb_mode.hasFocus():
            self.cmb_mode.setCurrentIndex(idx)

        names = [("HI_HI", pid.alarms.hi_hi), ("HI", pid.alarms.hi),
                 ("LO", pid.alarms.lo), ("LO_LO", pid.alarms.lo_lo),
                 ("DV_HI", pid.alarms.dv_hi), ("DV_LO", pid.alarms.dv_lo)]
        rows = [n for n, on in names
                if on and not pid.alarm_shelved.get(n.lower(), False)]
        self.alarm_area.setText(
            "\n".join(f"⚠  {n}" for n in rows[:3]))
        self.trend.sample()
        for w in (self.bar, self.out_bar, self.trend):
            w.update()


# ================================================================ the detail
def _field(value: str, width: int = 70) -> QLineEdit:
    e = QLineEdit(value)
    e.setFixedWidth(width)
    e.setFont(_f(8.5))
    e.setAlignment(Qt.AlignRight)
    return e


class LoopDetail(LiveDialog):
    def __init__(self, loop, parent=None) -> None:
        super().__init__(parent)
        self.loop = loop
        pid = loop.pid
        self.setWindowTitle(f"{loop.module}  loop detail")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(724)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)

        # ------------------------------------------------------------ header
        head = QGridLayout()
        head.addWidget(_lbl(loop.description, 7.5, colour=DIM,
                            align=Qt.AlignHCenter), 0, 1)
        head.addWidget(_lbl(loop.module, 13, True, align=Qt.AlignHCenter), 1, 1)
        head.addWidget(_lbl(loop.pv_tag, 7.5, colour=DIM,
                            align=Qt.AlignHCenter), 2, 1)
        self.alarm_icon = AlarmIcon()
        head.addWidget(self.alarm_icon, 0, 2, 2, 1, Qt.AlignRight)
        fp_btn = IconButton("group", "Faceplate",
                            lambda: parent.raise_() if parent else None)
        head.addWidget(fp_btn, 0, 3, 2, 1, Qt.AlignRight)
        head.setColumnStretch(1, 1)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ------------------------------------------------------ left column
        left = QVBoxLayout()
        left.addWidget(_lbl("Limits", 9, True))
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        self._limits = {}
        rows = [("Hi Hi Lim", "hi_hi_lim"), ("Hi Lim", "hi_lim"),
                ("Dev Hi Lim", "dv_hi_lim"), ("Dev Lo Lim", "dv_lo_lim"),
                ("Lo Lim", "lo_lim"), ("Lo Lo Lim", "lo_lo_lim"),
                ("Out Hi Lim", "out_hi_lim"), ("Out Lo Lim", "out_lo_lim"),
                ("ARW Hi Lim", "arw_hi_lim"), ("ARW Lo Lim", "arw_lo_lim"),
                ("SP Hi Lim", "sp_hi_lim"), ("SP Lo Lim", "sp_lo_lim"),
                ("Alm Hysteresis", "alarm_hys")]
        for r, (label, attr) in enumerate(rows):
            grid.addWidget(_lbl(label, 8), r, 0)
            e = _field(f"{getattr(pid, attr):g}")
            e.editingFinished.connect(lambda a=attr, w=None: None)
            e.editingFinished.connect(
                lambda a=attr, ed=e: self._set_float(a, ed))
            self._limits[attr] = e
            grid.addWidget(e, r, 1)
        left.addLayout(grid)

        left.addSpacing(6)
        left.addWidget(_lbl("Simulate", 9, True))
        sim_row = QGridLayout()
        self.chk_sim = QCheckBox("Simulate")
        self.chk_sim.setFont(_f(8))
        self.chk_sim.setChecked(pid.simulate_enable)
        self.chk_sim.toggled.connect(self._sim_toggled)
        sim_row.addWidget(self.chk_sim, 0, 0)
        self.ed_sim = _field(f"{pid.simulate_value:g}")
        self.ed_sim.editingFinished.connect(self._sim_value)
        sim_row.addWidget(self.ed_sim, 0, 1)
        sim_row.addWidget(_lbl("Field Value", 8), 1, 0)
        self.lbl_field = _lbl("-", 8.5, colour=DIM, align=Qt.AlignRight)
        sim_row.addWidget(self.lbl_field, 1, 1)
        left.addLayout(sim_row)
        left.addStretch(1)
        body.addLayout(left)

        # ---------------------------------------------------- tuning column
        mid = QVBoxLayout()
        tune_hdr = QHBoxLayout()
        tune_hdr.addWidget(_lbl("Tuning", 9, True))
        tune_hdr.addSpacing(30)
        tune_hdr.addWidget(_lbl("ADAPT", 8, colour="#9AA6B0"))
        tune_hdr.addStretch(1)
        mid.addLayout(tune_hdr)
        tg = QGridLayout()
        tg.setVerticalSpacing(2)
        tg.setColumnMinimumWidth(0, 84)
        self._tuning = {}
        for r, (label, attr) in enumerate([("Gain", "gain"),
                                           ("Reset", "reset"),
                                           ("Rate", "rate")]):
            tg.addWidget(_lbl(label, 8), r, 0)
            e = _field(f"{getattr(pid, attr):g}")
            e.editingFinished.connect(lambda a=attr, ed=e: self._set_float(a, ed))
            self._tuning[attr] = e
            tg.addWidget(e, r, 1)
            tg.addWidget(_lbl("-", 8.5, colour="#AEB7C0",
                              align=Qt.AlignRight), r, 2)
        for r, (label, attr, unit) in enumerate(
                [("PV Filter TC", "pv_ftime", "s"),
                 ("SP Filter TC", "sp_ftime", "s"),
                 ("SP Rate DN", "sp_rate_dn", "EU/s"),
                 ("SP Rate UP", "sp_rate_up", "EU/s"),
                 ("FF Gain", "ff_gain", "")], start=3):
            tg.addWidget(_lbl(label, 8), r, 0)
            e = _field(f"{getattr(pid, attr):g}")
            e.editingFinished.connect(lambda a=attr, ed=e: self._set_float(a, ed))
            self._tuning[attr] = e
            tg.addWidget(e, r, 1)
            tg.addWidget(_lbl(unit, 7.5, colour=DIM), r, 2)
        mid.addLayout(tg)

        srow = QGridLayout()
        srow.setColumnMinimumWidth(0, 84)
        srow.addWidget(_lbl("Structure", 8), 0, 0)
        self.cmb_struct = QComboBox()
        self.cmb_struct.setFont(_f(8))
        self.cmb_struct.setMinimumWidth(210)
        for st in Structure:
            self.cmb_struct.addItem(st.value)
        self.cmb_struct.setCurrentText(pid.structure.value)
        self.cmb_struct.activated.connect(
            lambda _i: setattr(pid, "structure",
                               Structure(self.cmb_struct.currentText())))
        srow.addWidget(self.cmb_struct, 0, 1, 1, 4)
        srow.addWidget(_lbl("Beta", 7.5, colour=DIM, align=Qt.AlignHCenter), 1, 1)
        srow.addWidget(_lbl("Gamma", 7.5, colour=DIM, align=Qt.AlignHCenter), 1, 2)
        eb = _field(f"{pid.beta:g}", 48)
        eb.editingFinished.connect(lambda ed=eb: self._set_float("beta", ed))
        eg = _field(f"{pid.gamma:g}", 48)
        eg.editingFinished.connect(lambda ed=eg: self._set_float("gamma", ed))
        srow.addWidget(eb, 2, 1)
        srow.addWidget(eg, 2, 2)
        chk = QCheckBox("Direct acting")
        chk.setFont(_f(8))
        chk.setChecked(pid.direct_acting)
        chk.toggled.connect(lambda v: setattr(pid, "direct_acting", bool(v)))
        srow.addWidget(chk, 3, 1, 1, 3)
        srow.addWidget(_lbl("Adaptive Mode", 8), 4, 0)
        adapt = QComboBox()
        adapt.setFont(_f(8))
        adapt.addItem("Do Not Adapt")
        adapt.setEnabled(False)
        srow.addWidget(adapt, 4, 1, 1, 3)
        mid.addLayout(srow)
        mid.addStretch(1)
        body.addLayout(mid)

        # ------------------------------------------- alarms and diagnostics
        right = QVBoxLayout()
        ag = QGridLayout()
        ag.setVerticalSpacing(1)
        ag.setHorizontalSpacing(8)
        ag.setColumnMinimumWidth(0, 48)
        ag.setColumnMinimumWidth(1, 92)
        for c in (2, 3, 4):
            ag.setColumnMinimumWidth(c, 32)
        ag.addWidget(_lbl("Alarms", 9, True), 0, 0)
        for c, cap in enumerate(("Enab", "OOS", "Shlv", "Help")):
            ag.addWidget(_lbl(cap, 7, colour=DIM, align=Qt.AlignHCenter),
                         0, 2 + c)
        self._alarm_widgets = {}
        prio = {"Hi Hi": "CRITICAL", "Hi": "WARNING", "Dev Hi": "WARNING",
                "Dev Lo": "WARNING", "Lo": "WARNING", "Lo Lo": "CRITICAL",
                "PV Bad": "CRITICAL"}
        keymap = {"Hi Hi": "hi_hi", "Hi": "hi", "Dev Hi": "dv_hi",
                  "Dev Lo": "dv_lo", "Lo": "lo", "Lo Lo": "lo_lo",
                  "PV Bad": "pv_bad"}
        for r, name in enumerate(prio, start=1):
            key = keymap[name]
            ag.addWidget(_lbl(name, 8), r, 0)
            cb_p = QComboBox()
            cb_p.setFont(_f(7.5))
            cb_p.addItems(["CRITICAL", "WARNING", "ADVISORY", "LOG"])
            cb_p.setCurrentText(prio[name])
            cb_p.setFixedWidth(88)
            ag.addWidget(cb_p, r, 1)
            en = QCheckBox()
            en.setChecked(pid.alarm_enab.get(key, True))
            en.toggled.connect(
                lambda v, k=key: pid.alarm_enab.__setitem__(k, bool(v)))
            oos = QCheckBox()
            shlv = QCheckBox()
            shlv.toggled.connect(
                lambda v, k=key: pid.alarm_shelved.__setitem__(k, bool(v)))
            for c, w in enumerate((en, oos, shlv)):
                ag.addWidget(w, r, 2 + c, Qt.AlignHCenter)
            help_b = _lbl("?", 8, True, WHITE, Qt.AlignCenter)
            help_b.setFixedSize(13, 13)
            help_b.setStyleSheet(f"background: {ADVISORY}; color: {WHITE};"
                                 "border-radius: 6px;")
            help_b.setToolTip(f"{name} alarm on {loop.pv_tag}")
            ag.addWidget(help_b, r, 5, Qt.AlignHCenter)
        pr = QHBoxLayout()
        pr.addWidget(_lbl("Priority Adj", 8))
        adj = QComboBox()
        adj.setFont(_f(8))
        adj.addItems(["0", "+1", "-1"])
        adj.setFixedWidth(46)
        pr.addWidget(adj)
        pr.addStretch(1)
        right.addLayout(ag)
        right.addLayout(pr)

        right.addWidget(_lbl("Diagnostics", 9, True))
        self.tabs = QTabWidget()
        self.tabs.setFixedHeight(120)
        self.diag = QLabel("Module OK")
        self.diag.setFont(_f(8.5))
        self.diag.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.diag.setMargin(6)
        self.diag.setStyleSheet(f"background: {WHITE};")
        clear_wrap = QWidget()
        cw = QVBoxLayout(clear_wrap)
        cw.setContentsMargins(4, 4, 4, 4)
        btn = QPushButton("Clear Error")
        btn.setFont(_f(8))
        btn.clicked.connect(lambda: None)
        cw.addWidget(self.diag, 1)
        cw.addWidget(btn, 0, Qt.AlignRight)
        self.tabs.addTab(clear_wrap, "MERROR")
        for name in ("MSTATUS", "BLOCKERR"):
            page = QLabel("No entries")
            page.setFont(_f(8.5))
            page.setMargin(6)
            page.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.tabs.addTab(page, name)
        right.addWidget(self.tabs)
        right.addStretch(1)
        body.addLayout(right)
        outer.addLayout(body)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(700)
        self._refresh()

    def _set_float(self, attr: str, w: QLineEdit) -> None:
        try:
            setattr(self.loop.pid, attr, float(w.text()))
        except ValueError:
            w.setText(f"{getattr(self.loop.pid, attr):g}")

    def _sim_toggled(self, on: bool) -> None:
        pid = self.loop.pid
        if on and not pid.simulate_enable:
            pid.simulate_value = pid.pv
            self.ed_sim.setText(f"{pid.simulate_value:g}")
        pid.simulate_enable = bool(on)

    def _sim_value(self) -> None:
        try:
            self.loop.pid.simulate_value = float(self.ed_sim.text())
        except ValueError:
            pass

    def _refresh(self) -> None:
        pid = self.loop.pid
        self.lbl_field.setText(f"{pid.field_value:.2f}")
        self.alarm_icon.active = pid.alarms.any_active() or not pid.pv_good
        self.alarm_icon.update()
        lines = []
        if not pid.pv_good:
            lines.append("Input failure - PV status Bad")
        if pid.simulate_enable:
            lines.append("Simulate active")
        if pid.actual_mode is Mode.OOS:
            lines.append("Out of Service")
        self.diag.setText("\n".join(lines) if lines else "Module OK")
        self.diag.setStyleSheet(
            f"background: {WHITE}; color: "
            f"{ALARM_RED if lines and not pid.simulate_enable else TXT};")
