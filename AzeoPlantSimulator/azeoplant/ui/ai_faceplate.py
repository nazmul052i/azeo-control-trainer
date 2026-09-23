# -*- coding: utf-8 -*-
"""Analog monitoring faceplate and detail, with the shared measured-value controls.

Any analogue indication that is not the PV of a control module gets this
display: the AI_fp with the PV value coloured by signal status, the PV bar
graph with the alarm limits, actual mode, condensed trend and active alarm
list; and the AI_dt with the limits, simulate, PV filter and the five-row
alarm table. The monitor object owns the alarm state so limits and
priorities survive the dialog being closed and reopened.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core.tags import Quality, Tag
from .live_dialog import LiveDialog, retain_detail
from .loop_faceplate import (ADVISORY, ALARM_RED, DIM, FRAME, OUT_TEAL, PANEL,
                             PANEL_DK, PV_BLUE, STYLE, TXT, WHITE, AlarmIcon,
                             Chevron, IconButton, SimBadge, _f, _field, _lbl)

WARNING = "#E8A317"

BAR_BG = "#8FA6C3"        # the medium blue of the AI bar background
BAR_FILL = "#3B618D"      # the dark PV strip
BAND_HIHI = "#E3D5B3"     # the tan band above the HIHI limit
LIMIT_MARK = "#EFEFEF"    # limit lines, pale until that alarm is active

_ALARMS = ("HI_HI", "HI", "LO", "LO_LO", "PV_BAD")


# ============================================================== the monitor
class AnalogMonitor:
    """The ANALOG module behind one AI tag: limits, alarms, simulate.

    The DCS owns real alarming; this is the module the faceplate displays
    and configures, evaluated at display rate with the same hysteresis
    behaviour the PID block uses.
    """

    def __init__(self, tag: Tag) -> None:
        self.tag = tag
        span = tag.span
        self.hi_hi_lim = tag.lo + 0.95 * span
        self.hi_lim = tag.lo + 0.90 * span
        self.lo_lim = tag.lo + 0.10 * span
        self.lo_lo_lim = tag.lo + 0.05 * span
        self.alm_hyst = 0.5            # percent of span
        self.low_cutoff = float("nan")
        self.pv_ftime = 0.0            # seconds, exponential PV filter
        self.simulate_enable = False
        self.simulate_value = float(tag.value)
        self._pv = float(tag.value)
        self.priority = {"HI_HI": "CRITICAL", "HI": "WARNING",
                         "LO": "WARNING", "LO_LO": "CRITICAL",
                         "PV_BAD": "CRITICAL"}
        self.enab = {a: True for a in _ALARMS}
        self.oos = {a: False for a in _ALARMS}
        self.shlv = {a: False for a in _ALARMS}
        self._active = {a: False for a in _ALARMS}
        # The configured alarm schedule takes precedence over the default
        # percent-of-span limits wherever the sheet names this tag.
        try:
            from ..control.alarm_table import ALARMS
        except ImportError:
            ALARMS = {}
        pri_map = {"Critical": "CRITICAL", "High": "WARNING",
                   "Advisory": "ADVISORY"}
        for typ, sp, pri, dead, _delay in ALARMS.get(tag.name, []):
            if typ in ("HI_HI", "HI", "LO", "LO_LO"):
                attr = typ.lower() + "_lim"
                setattr(self, attr, float(sp))
                self.priority[typ] = pri_map.get(pri, "WARNING")
                if dead > 0:
                    self.alm_hyst = max(self.alm_hyst,
                                        dead / span * 100.0)

    # ------------------------------------------------------------- reading
    @property
    def pv(self) -> float:
        return self._pv

    @property
    def pv_good(self) -> bool:
        if self.simulate_enable:
            return True
        return self.tag.quality is Quality.GOOD

    def update(self, dt: float = 0.4) -> None:
        """Advance the filter and the alarm hysteresis one display tick."""
        raw = (self.simulate_value if self.simulate_enable
               else float(self.tag.value))
        if self.pv_ftime > 1e-6:
            a = 1.0 - math.exp(-dt / self.pv_ftime)
            self._pv += a * (raw - self._pv)
        else:
            self._pv = raw
        hyst = self.alm_hyst / 100.0 * self.tag.span
        pv = self._pv
        for name, lim, high in (("HI_HI", self.hi_hi_lim, True),
                                ("HI", self.hi_lim, True),
                                ("LO", self.lo_lim, False),
                                ("LO_LO", self.lo_lo_lim, False)):
            if not math.isfinite(lim):
                self._active[name] = False
                continue
            if high:
                if pv >= lim:
                    self._active[name] = True
                elif pv < lim - hyst:
                    self._active[name] = False
            else:
                if pv <= lim:
                    self._active[name] = True
                elif pv > lim + hyst:
                    self._active[name] = False
        self._active["PV_BAD"] = not self.pv_good

    def active_alarms(self) -> List[str]:
        """Active, enabled, not out-of-service alarms, worst first."""
        order = {"CRITICAL": 0, "WARNING": 1, "ADVISORY": 2}
        live = [a for a in _ALARMS
                if self._active[a] and self.enab[a] and not self.oos[a]]
        return sorted(live, key=lambda a: order[self.priority[a]])

    def limit_of(self, name: str) -> float:
        return {"HI_HI": self.hi_hi_lim, "HI": self.hi_lim,
                "LO": self.lo_lim, "LO_LO": self.lo_lo_lim}.get(
                    name, float("nan"))


def _monitor_for(window, tag_name: str) -> AnalogMonitor:
    """The per-window monitor registry, created lazily and kept forever."""
    if not hasattr(window, "_ai_monitors"):
        window._ai_monitors = {}
    reg: Dict[str, AnalogMonitor] = window._ai_monitors
    if tag_name not in reg:
        reg[tag_name] = AnalogMonitor(window.db[tag_name])
    return reg[tag_name]


# ================================================================== the bar
class AiBar(QWidget):
    """The AI_fp PV bar graph: thin scale line with divisions at the left,
    the medium blue bar with the tan HIHI band, the narrow dark PV strip,
    and the pale limit lines that take the alarm colour only while that
    alarm is active."""

    def __init__(self, mon: AnalogMonitor) -> None:
        super().__init__()
        self.mon = mon
        self.setFixedSize(52, 214)

    def paintEvent(self, ev) -> None:  # noqa: N802
        mon = self.mon
        tag = mon.tag
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        top, bot = 13.0, h - 13.0
        span_px = bot - top

        def y_of(eu: float) -> float:
            f = (eu - tag.lo) / tag.span
            return bot - max(0.0, min(1.0, f)) * span_px

        # scale line and divisions, left of the bar
        p.setPen(QPen(QColor("#A0A0A0"), 1))
        p.drawLine(QPointF(14, top), QPointF(14, bot))
        p.setPen(QPen(QColor("#AAAAAA"), 1))
        for i in range(13):
            y = top + span_px * i / 12.0
            major = i % 3 == 0
            p.drawLine(QPointF(6 if major else 9, y), QPointF(14, y))

        # the bar: blue background, tan band above HIHI, dark PV strip
        bar = QRectF(15, top, 17, span_px)
        p.fillRect(bar, QColor(BAR_BG))
        if math.isfinite(mon.hi_hi_lim):
            y = y_of(mon.hi_hi_lim)
            p.fillRect(QRectF(bar.left(), top, bar.width(), y - top),
                       QColor(BAND_HIHI))
        y_pv = y_of(mon.pv)
        p.fillRect(QRectF(20, y_pv, 7, bot - y_pv), QColor(BAR_FILL))

        # limit lines across the bar, pale unless that alarm is active
        for name, colour in (("HI_HI", ALARM_RED), ("HI", WARNING),
                             ("LO", WARNING), ("LO_LO", ALARM_RED)):
            lim = mon.limit_of(name)
            if math.isfinite(lim) and tag.lo <= lim <= tag.hi:
                on = mon._active[name] and mon.enab[name]
                p.setPen(QPen(QColor(colour if on else LIMIT_MARK), 2.5))
                y = y_of(lim)
                p.drawLine(QPointF(11, y), QPointF(37, y))

        # EU100 over, EU0 under
        p.setPen(QPen(QColor(DIM)))
        p.setFont(_f(7))
        p.drawText(QRectF(0, 0, w, 11), Qt.AlignHCenter, f"{tag.hi:g}")
        p.drawText(QRectF(0, h - 11, w, 11), Qt.AlignHCenter, f"{tag.lo:g}")
        p.end()


# ================================================================ the trend
class AiTrend(QWidget):
    """The condensed trend: grey field, EU axis labels, the PV polyline."""

    def __init__(self, mon: AnalogMonitor) -> None:
        super().__init__()
        self.mon = mon
        self.setFixedHeight(54)
        self._hist: List[float] = []

    def sample(self) -> None:
        self._hist.append(self.mon.pv)
        if len(self._hist) > 300:
            self._hist = self._hist[-300:]

    def paintEvent(self, ev) -> None:  # noqa: N802
        tag = self.mon.tag
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#D6D6D6"))
        p.setPen(QPen(QColor("#999999"), 1))
        p.drawLine(QPointF(0, 0), QPointF(w, 0))
        p.setPen(QPen(QColor("#777777"), 1))
        p.drawLine(QPointF(0, h - 1), QPointF(w, h - 1))

        p.setPen(QPen(QColor("#315A8D")))
        p.setFont(_f(7))
        p.drawText(QRectF(2, 3, 30, 10), Qt.AlignLeft, f"{tag.hi:g}")
        p.drawText(QRectF(2, h - 13, 30, 10), Qt.AlignLeft, f"{tag.lo:g}")

        field = QRectF(26, 6, w - 34, h - 12)
        if len(self._hist) >= 2:
            pts = []
            n = len(self._hist)
            for i, v in enumerate(self._hist):
                x = field.left() + field.width() * i / (n - 1)
                f = (v - tag.lo) / tag.span
                y = field.bottom() - max(0.0, min(1.0, f)) * field.height()
                pts.append(QPointF(x, y))
            p.setPen(QPen(QColor("#315A8D"), 1.2))
            p.drawPolyline(QPolygonF(pts))

        # the resize corner glyph, drawn
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#8C97A3")))
        p.drawPolygon(QPolygonF([QPointF(w - 3, 6), QPointF(w - 3, 13),
                                 QPointF(w - 10, 13)]))
        p.end()


# ============================================================ the faceplate
class AiFaceplate(LiveDialog):
    def __init__(self, mon: AnalogMonitor, parent=None) -> None:
        super().__init__(parent)
        self.mon = mon
        tag = mon.tag
        self.setWindowTitle(f"{tag.name}  faceplate")
        self.setFixedWidth(208)
        self.setStyleSheet(STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(2)

        root.addWidget(_lbl(tag.desc, 7.5, colour=DIM,
                            align=Qt.AlignHCenter))
        root.addWidget(_lbl(tag.name, 13, True, align=Qt.AlignHCenter))
        root.addWidget(_lbl(tag.unit, 7.5, colour=DIM,
                            align=Qt.AlignHCenter))

        # ------------------------------------ PV block with S and chevron
        pv_row = QHBoxLayout()
        pv_col = QVBoxLayout()
        pv_col.setSpacing(0)
        self.lbl_pv = _lbl("-", 13, True, PV_BLUE, Qt.AlignHCenter)
        pv_col.addWidget(self.lbl_pv)
        self.lbl_eu = _lbl(tag.eu, 7.5, colour=DIM, align=Qt.AlignHCenter)
        pv_col.addWidget(self.lbl_eu)
        self.lbl_pct = _lbl("-", 7.5, colour=DIM, align=Qt.AlignHCenter)
        pv_col.addWidget(self.lbl_pct)
        pv_row.addStretch(1)
        pv_row.addLayout(pv_col)
        pv_row.addStretch(1)
        side = QVBoxLayout()
        side.setSpacing(1)
        self.sim_badge = SimBadge()
        side.addWidget(self.sim_badge)
        side.addWidget(Chevron())
        side.addStretch(1)
        pv_row.addLayout(side)
        root.addLayout(pv_row)

        # ---------------------------------------- bar with the actual mode
        bar_row = QHBoxLayout()
        bar_row.addStretch(2)
        self.bar = AiBar(mon)
        bar_row.addWidget(self.bar)
        mode_col = QVBoxLayout()
        mode_col.addStretch(1)
        mrow = QHBoxLayout()
        mrow.setSpacing(3)
        mark = _lbl("!", 7.5, True, WHITE, Qt.AlignCenter)
        mark.setFixedSize(10, 12)
        mark.setStyleSheet(f"background: {ADVISORY}; color: {WHITE};"
                           "border-radius: 2px;")
        mrow.addWidget(mark)
        mrow.addWidget(_lbl("Auto", 8.5, colour=TXT))
        mode_col.addLayout(mrow)
        mode_col.addStretch(1)
        bar_row.addLayout(mode_col)
        bar_row.addStretch(1)
        root.addLayout(bar_row)

        self.trend = AiTrend(mon)
        root.addWidget(self.trend)

        # -------------------------------------------------- the alarm list
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("Ack", 7.5, colour=DIM))
        hdr.addWidget(_lbl("Param", 7.5, colour=DIM), 1)
        hdr.addWidget(_lbl("Help", 7.5, colour=DIM))
        root.addLayout(hdr)
        self.alarm_area = QLabel()
        self.alarm_area.setFont(_f(8))
        self.alarm_area.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.alarm_area.setStyleSheet("background: transparent; border: none;")
        box = QWidget()
        box.setFixedHeight(40)
        box.setStyleSheet(f"background: {WHITE}; border: 1px solid {FRAME};")
        box_lay = QHBoxLayout(box)
        box_lay.setContentsMargins(3, 3, 3, 3)
        box_lay.setSpacing(4)
        self.alarm_pri = AlarmIcon()
        self.alarm_pri.setStyleSheet("background: transparent; border: none;")
        box_lay.addWidget(self.alarm_pri, 0, Qt.AlignTop)
        box_lay.addWidget(self.alarm_area, 1)
        wrap = QHBoxLayout()
        wrap.setSpacing(0)
        wrap.addWidget(box, 1)
        rail = QVBoxLayout()
        rail.setSpacing(0)
        for arrow in ("▲", "▼"):
            a = _lbl(arrow, 6, colour=DIM, align=Qt.AlignCenter)
            a.setFixedSize(13, 20)
            a.setStyleSheet(f"background: {PANEL_DK};"
                            f"border: 1px solid {FRAME};")
            rail.addWidget(a)
        wrap.addLayout(rail)
        root.addLayout(wrap)

        urow = QHBoxLayout()
        urow.addWidget(_lbl("Unit:", 8, colour=DIM))
        urow.addWidget(_lbl(tag.unit, 8, True), 1)
        root.addLayout(urow)

        icons = QHBoxLayout()
        icons.setSpacing(6)
        icons.addWidget(IconButton("detail", "Detail display",
                                   self._open_detail))
        icons.addWidget(IconButton("group", "Faceplate group", None, False))
        icons.addWidget(IconButton("sliders", "Tune", None, False))
        icons.addWidget(IconButton("list", "Module explorer", None, False))
        icons.addWidget(IconButton("bell", "Acknowledge alarms",
                                   self._ack))
        icons.addStretch(1)
        root.addLayout(icons)

        self._detail: Optional[AiDetail] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(400)
        self._refresh()

    def _open_detail(self) -> None:
        if self._detail is None:
            retain_detail(self, AiDetail(self.mon, self))
        else:
            self._detail.raise_()
            self._detail.activateWindow()
        self._detail.show()

    def _ack(self) -> None:
        alarms = getattr(self.parent(), "alarms", None)
        if alarms is not None:
            alarms.ack_tag(self.mon.tag.name)

    def _refresh(self) -> None:
        mon = self.mon
        mon.update()
        tag = mon.tag
        good = mon.pv_good
        dp = 2 if tag.span < 100 else 1 if tag.span < 1000 else 0
        self.lbl_pv.setText(f"{mon.pv:.{dp}f}")
        self.lbl_pv.setStyleSheet(
            f"color: {PV_BLUE if good else ALARM_RED};"
            "background: transparent;")
        self.lbl_pct.setText(f"{(mon.pv - tag.lo) / tag.span * 100.0:.1f} %")
        self.sim_badge.active = mon.simulate_enable
        self.sim_badge.update()
        live = mon.active_alarms()
        if live:
            worst = live[0]
            self.alarm_pri.active = True
            self.alarm_pri.priority = mon.priority[worst]
            self.alarm_pri.suppressed = mon.shlv[worst]
            names = "\n".join(f"{a}_ALM" for a in live[:2])
            self.alarm_area.setText(names)
        else:
            self.alarm_pri.active = False
            self.alarm_area.setText("")
        self.alarm_pri.update()
        self.bar.update()
        self.trend.sample()
        self.trend.update()


# =============================================================== the detail
class AiDetail(LiveDialog):
    def __init__(self, mon: AnalogMonitor, parent=None) -> None:
        super().__init__(parent)
        self.mon = mon
        tag = mon.tag
        self.setWindowTitle(f"{tag.name}  detail")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.addStretch(1)
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(_lbl(tag.desc, 7.5, colour=DIM,
                             align=Qt.AlignHCenter))
        title.addWidget(_lbl(tag.name, 13, True, align=Qt.AlignHCenter))
        title.addWidget(_lbl(tag.unit, 7.5, colour=DIM,
                             align=Qt.AlignHCenter))
        head.addLayout(title)
        head.addStretch(1)
        self.alarm_icon = AlarmIcon()
        head.addWidget(self.alarm_icon, 0, Qt.AlignTop)
        head.addWidget(IconButton("group", "Faceplate", self._to_faceplate),
                       0, Qt.AlignTop)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ----------------------------------------------------- left column
        left = QVBoxLayout()
        lt = QHBoxLayout()
        lt.addWidget(_lbl("Limits", 9, True))
        more = QPushButton("...")
        more.setFont(_f(8, True))
        more.setFixedSize(26, 16)
        more.setEnabled(False)
        lt.addWidget(more)
        lt.addStretch(1)
        left.addLayout(lt)

        lim = QGridLayout()
        lim.setVerticalSpacing(2)
        self._eds: Dict[str, QLineEdit] = {}

        def lim_row(r: int, cap: str, attr: str, unit: str = "") -> None:
            lim.addWidget(_lbl(cap, 8), r, 0)
            v = getattr(mon, attr)
            e = _field("" if not math.isfinite(v) else f"{v:g}", 64)
            e.editingFinished.connect(lambda a=attr, w=e: self._setf(a, w))
            self._eds[attr] = e
            lim.addWidget(e, r, 1)
            lim.addWidget(_lbl(unit, 7.5, colour=DIM), r, 2)

        lim_row(0, "Hi Hi Lim", "hi_hi_lim", tag.eu)
        lim_row(1, "Hi Lim", "hi_lim", tag.eu)
        lim_row(2, "Lo Lim", "lo_lim", tag.eu)
        lim_row(3, "Lo Lo Lim", "lo_lo_lim", tag.eu)
        lim_row(4, "Alm Hysteresis", "alm_hyst", "%")
        lim_row(5, "Low Cutoff", "low_cutoff", tag.eu)
        left.addLayout(lim)

        left.addSpacing(8)
        left.addWidget(_lbl("Simulate", 9, True))
        sim = QGridLayout()
        sim.setVerticalSpacing(2)
        self.chk_sim = QCheckBox("Simulate")
        self.chk_sim.setFont(_f(8))
        self.chk_sim.setChecked(mon.simulate_enable)
        self.chk_sim.toggled.connect(
            lambda v: setattr(self.mon, "simulate_enable", bool(v)))
        sim.addWidget(self.chk_sim, 0, 0)
        self.ed_sim = _field(f"{mon.simulate_value:g}", 64)
        self.ed_sim.editingFinished.connect(self._simv)
        sim.addWidget(self.ed_sim, 0, 1)
        sim.addWidget(_lbl(tag.eu, 7.5, colour=DIM), 0, 2)
        sim.addWidget(_lbl("Field Value", 8), 1, 0)
        self.lbl_fv = _lbl("-", 8.5, colour=PV_BLUE, align=Qt.AlignRight)
        sim.addWidget(self.lbl_fv, 1, 1)
        sim.addWidget(_lbl("%", 7.5, colour=DIM), 1, 2)
        left.addLayout(sim)

        left.addSpacing(8)
        left.addWidget(_lbl("Tuning", 9, True))
        tun = QGridLayout()
        tun.addWidget(_lbl("PV Filter TC", 8), 0, 0)
        e = _field(f"{mon.pv_ftime:g}", 64)
        e.editingFinished.connect(lambda w=e: self._setf("pv_ftime", w))
        self._eds["pv_ftime"] = e
        tun.addWidget(e, 0, 1)
        tun.addWidget(_lbl("s", 7.5, colour=DIM), 0, 2)
        left.addLayout(tun)

        left.addSpacing(8)
        left.addWidget(_lbl("Linearization", 9, True))
        left.addWidget(_lbl("Linear, direct", 8, colour=DIM))
        left.addStretch(1)
        body.addLayout(left)

        # ----------------------------------------------------- right column
        right = QVBoxLayout()
        arow = QGridLayout()
        arow.setVerticalSpacing(2)
        arow.addWidget(_lbl("Alarms", 9, True), 0, 0)
        arow.addWidget(_lbl("Priority", 7, colour=DIM,
                            align=Qt.AlignHCenter), 0, 1)
        for c, cap in enumerate(("Enab", "OOS", "Shlv", "Help")):
            arow.addWidget(_lbl(cap, 7, colour=DIM, align=Qt.AlignHCenter),
                           0, 2 + c)

        caps = {"HI_HI": "Hi Hi", "HI": "Hi", "LO": "Lo",
                "LO_LO": "Lo Lo", "PV_BAD": "PV Bad"}
        for r, name in enumerate(_ALARMS, start=1):
            arow.addWidget(_lbl(caps[name], 8), r, 0)
            cb = QComboBox()
            cb.setFont(_f(7.5))
            cb.addItems(["CRITICAL", "WARNING", "ADVISORY"])
            cb.setCurrentText(mon.priority[name])
            cb.currentTextChanged.connect(
                lambda s, a=name: self.mon.priority.__setitem__(a, s))
            cb.setFixedWidth(88)
            arow.addWidget(cb, r, 1)
            for c, d in enumerate((mon.enab, mon.oos, mon.shlv)):
                k = QCheckBox()
                k.setChecked(d[name])
                k.toggled.connect(
                    lambda v, dd=d, a=name: dd.__setitem__(a, bool(v)))
                arow.addWidget(k, r, 2 + c, Qt.AlignHCenter)
            h = _lbl("?", 8, True, WHITE, Qt.AlignCenter)
            h.setFixedSize(13, 13)
            h.setStyleSheet(f"background: {ADVISORY}; color: {WHITE};"
                            "border-radius: 6px;")
            arow.addWidget(h, r, 5, Qt.AlignHCenter)

        arow.addWidget(_lbl("Priority Adj", 8), 6, 0)
        adj = QComboBox()
        adj.setFont(_f(8))
        adj.addItems(["0", "+1", "-1"])
        adj.setFixedWidth(46)
        arow.addWidget(adj, 6, 1, Qt.AlignLeft)
        right.addLayout(arow)

        right.addSpacing(6)
        right.addWidget(_lbl("Diagnostics", 9, True))
        self.tabs = QTabWidget()
        self.tabs.setFixedHeight(120)
        self.diag = QLabel("Module OK")
        for name in ("MERROR", "MSTATUS", "BLOCKERR"):
            pg = QLabel() if name != "MERROR" else self.diag
            pg.setFont(_f(8))
            pg.setStyleSheet(f"background: {WHITE};")
            pg.setMargin(6)
            pg.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            if name != "MERROR":
                pg.setText("OK")
            self.tabs.addTab(pg, name)
        right.addWidget(self.tabs)
        crow = QHBoxLayout()
        crow.addStretch(1)
        clr = QPushButton("Clear Error")
        clr.setFont(_f(8.5, True))
        clr.setFixedSize(90, 22)
        clr.setEnabled(False)
        crow.addWidget(clr)
        right.addLayout(crow)
        right.addStretch(1)
        body.addLayout(right)
        outer.addLayout(body)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(600)
        self._refresh()

    def _to_faceplate(self) -> None:
        parent = self.parent()
        if parent is not None:
            parent.raise_()
            parent.activateWindow()

    def _setf(self, attr: str, ed: QLineEdit) -> None:
        try:
            setattr(self.mon, attr, float(ed.text()))
        except ValueError:
            v = getattr(self.mon, attr)
            ed.setText("" if not math.isfinite(v) else f"{v:g}")

    def _simv(self) -> None:
        try:
            self.mon.simulate_value = float(self.ed_sim.text())
        except ValueError:
            pass

    def _refresh(self) -> None:
        mon = self.mon
        tag = mon.tag
        self.lbl_fv.setText(f"{tag.pct:.1f}")
        live = mon.active_alarms()
        if live:
            worst = live[0]
            self.alarm_icon.active = True
            self.alarm_icon.priority = mon.priority[worst]
            self.alarm_icon.suppressed = mon.shlv[worst]
        else:
            self.alarm_icon.active = False
        self.alarm_icon.update()
        bad = not mon.pv_good
        self.diag.setText("IO Input Error\nFunction Block Bad Active"
                          if bad else "Module OK")
        self.diag.setStyleSheet(
            f"background: {WHITE}; color: {ALARM_RED if bad else TXT};")
