# -*- coding: utf-8 -*-
"""Motor and valve faceplate and detail, with shared operator controls and detail tabs.

One adapter covers the plant's three discrete device families - MotorPackage,
MovPackage and SdvPackage - and everything on the displays is live plant
truth: the state buttons write the same XY command tags a DCS writes, the
transition timer is the device's real travel or start delay, Reset clears the
real latched fault, SIMULATE really substitutes the feedback (limit switches
or run contact) while the device moves underneath, and the interlock rows
show the conditions that actually hold the device, first-out arrow included.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..models.packages import MotorPackage, MovPackage, PumpTrain, SdvPackage
from .live_dialog import LiveDialog, retain_detail
from .loop_faceplate import (ADVISORY, ALARM_RED, DIM, FRAME, OUT_TEAL, PANEL,
                             PANEL_DK, PV_BLUE, STYLE, TXT, WHITE, AlarmIcon,
                             Chevron, IconButton, SimBadge, _f, _field, _lbl)

WARNING = "#E8A317"


# ============================================================== the adapter
class DeviceAdapter:
    """A uniform face over a motor, an MOV or an SDV package."""

    def __init__(self, tag: str, pkg, unit_code: str, service: str) -> None:
        self.tag = tag
        self.pkg = pkg
        self.unit = unit_code
        self.service = service
        self.kind = type(pkg).__name__
        self.fail_priority = "CRITICAL"
        self.fail_enab = True
        self.fail_shelved = False

    # ------------------------------------------------------------ identity
    @property
    def is_motor(self) -> bool:
        return hasattr(self.pkg, "cmd_start")

    @property
    def is_mov(self) -> bool:
        return hasattr(self.pkg, "cmd_open") and hasattr(self.pkg, "trq")

    # --------------------------------------------------------------- state
    def state_text(self) -> str:
        if self.is_motor:
            d = self.pkg.device
            if d.faulted:
                return "Tripped"
            if d.running and d.speed_pct < 95.0:
                return "Starting"
            return "Running" if d.running else "Stopped"
        if self.is_mov:
            return str(self.pkg.device.state)
        pos = self.pkg.position
        return "Open" if pos >= 99.0 else "Closed" if pos <= 1.0 else "Traveling"

    def pv_percent(self) -> float:
        if self.is_motor:
            return self.pkg.device.speed_pct
        return self.pkg.device.position if self.is_mov else self.pkg.position

    def failed(self) -> bool:
        if self.is_motor:
            return self.pkg.device.faulted
        if self.is_mov:
            return self.pkg.device.torque_tripped
        return False

    def fail_text(self) -> str:
        if self.is_motor and self.pkg.device.faulted:
            return "Motor fault trip"
        if self.is_mov and self.pkg.device.torque_tripped:
            return "Torque switch trip"
        return ""

    def reset(self) -> None:
        if self.is_motor:
            self.pkg.device.reset()
        elif self.is_mov:
            self.pkg.device.torque_tripped = False

    # ------------------------------------------------------------ commands
    def states(self) -> List[Tuple[str, Callable[[], None]]]:
        if self.is_motor:
            return [("START", lambda: self._cmd(self.pkg.cmd_start, True,
                                               self.pkg.cmd_stop)),
                    ("STOP", lambda: self._cmd(self.pkg.cmd_stop, True,
                                              self.pkg.cmd_start))]
        if self.is_mov:
            return [("OPEN", lambda: self._cmd(self.pkg.cmd_open, True,
                                              self.pkg.cmd_close)),
                    ("STOP", self._mov_stop),
                    ("CLOSE", lambda: self._cmd(self.pkg.cmd_close, True,
                                               self.pkg.cmd_open))]
        return [("OPEN", lambda: setattr(self.pkg.cmd, "value", True)),
                ("CLOSE", lambda: setattr(self.pkg.cmd, "value", False))]

    @staticmethod
    def _cmd(tag_on, value, tag_off) -> None:
        tag_on.value = bool(value)
        tag_off.value = False

    def _mov_stop(self) -> None:
        self.pkg.cmd_open.value = False
        self.pkg.cmd_close.value = False

    # ------------------------------------------------------------- timings
    def transition(self) -> Tuple[float, float]:
        """(time limit, elapsed) for the current state transition."""
        if self.is_motor:
            d = self.pkg.device
            return d.start_delay, d._timer
        d = self.pkg.device if self.is_mov else self.pkg
        limit = d.travel_time if self.is_mov else self.pkg.stroke
        pos = d.position if self.is_mov else self.pkg.position
        moving = 0.5 < pos < 99.5
        frac = min(pos, 100.0 - pos) / 100.0 if moving else 0.0
        return limit, limit * frac * 2.0 if moving else 0.0

    def delayed(self) -> float:
        if self.is_motor:
            return self.pkg.device.coast_time
        return 0.0

    # ------------------------------------------------------------------ io
    def io(self) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        b = lambda t: "1" if bool(t.value) else "0"
        if self.is_motor:
            ins = [("RUN", b(self.pkg.run)), ("FLT", b(self.pkg.flt)),
                   ("AVL", b(self.pkg.avl))]
            if self.pkg.vfd_ok is not None:
                ins.append(("VFD", b(self.pkg.vfd_ok)))
            outs = [("STR", b(self.pkg.cmd_start)), ("STP", b(self.pkg.cmd_stop))]
        elif self.is_mov:
            ins = [("ZSO", b(self.pkg.zso)), ("ZSC", b(self.pkg.zsc)),
                   ("TRQ", b(self.pkg.trq)), ("AVL", b(self.pkg.avl))]
            outs = [("OPN", b(self.pkg.cmd_open)), ("CLS", b(self.pkg.cmd_close))]
        else:
            ins = [("ZSO", b(self.pkg.zso)), ("ZSC", b(self.pkg.zsc))]
            outs = [("OPN", b(self.pkg.cmd))]
        return ins, outs

    def field_value(self) -> str:
        if self.is_motor:
            return f"{self.pkg.device.current:.0f} A"
        return f"{self.pv_percent():.1f} %"

    # ---------------------------------------------------------- interlocks
    def interlocks(self) -> List[Tuple[str, bool]]:
        rows = []
        if self.is_motor:
            d = self.pkg.device
            rows.append(("Motor fault or thermal trip latched", d.faulted))
            rows.append(("Local switch, not available to the DCS",
                         self.pkg.local))
            if d.vfd:
                rows.append(("VFD communications fault", d.vfd_comms_fault))
        elif self.is_mov:
            d = self.pkg.device
            rows.append(("Torque switch trip", d.torque_tripped))
            rows.append(("Local switch, not available to the DCS",
                         self.pkg.local))
        else:
            rows.append(("ESD trip de-energises the valve",
                         self.state_text() == ("Open" if self.pkg.fail_open
                                               else "Closed")
                         and not bool(self.pkg.cmd.value)))
        return rows

    # ------------------------------------------------------------ simulate
    @property
    def can_simulate(self) -> bool:
        return hasattr(self.pkg, "simulate_enable")


class _RawShim:
    """Wraps a bare Motor or MotorOperatedValve (the pre-package units wire
    their IO tags by hand) so the adapter sees the package interface."""

    def __init__(self, device, db) -> None:
        self.device = device
        self.tag = device.tag
        self.local = False
        self.simulate_enable = False
        self.simulate_value = 0.0
        b = device.tag.replace("-", "")
        g = lambda name: db.get(name)
        from ..core.devices import Motor
        self.service = ("Pump motor" if isinstance(device, Motor)
                        else "Motor operated valve")
        if isinstance(device, Motor):
            self.run = g(f"XS-{b}-RUN")
            self.flt = g(f"XS-{b}-FLT")
            self.avl = g(f"XS-{b}-AVL")
            self.vfd_ok = g(f"XS-{b}-VFD")
            self.cmd_start = g(f"XY-{b}-STR")
            self.cmd_stop = g(f"XY-{b}-STP")
        else:
            self.zso = g(f"ZSO-{b}")
            self.zsc = g(f"ZSC-{b}")
            self.trq = g(f"XS-{b}-TRQ")
            self.avl = g(f"XS-{b}-AVL")
            self.cmd_open = g(f"XY-{b}-OPN")
            self.cmd_close = g(f"XY-{b}-CLS")

    def complete(self) -> bool:
        needed = (("run", "cmd_start", "cmd_stop") if hasattr(self, "run")
                  else ("zso", "zsc", "cmd_open", "cmd_close"))
        return all(getattr(self, n, None) is not None for n in needed)


def build_device_registry(flowsheet) -> Dict[str, DeviceAdapter]:
    """Every discrete device in the plant, keyed by tag and by its IO tags."""
    reg: Dict[str, DeviceAdapter] = {}

    def add(pkg, unit_code: str) -> None:
        tag = getattr(pkg, "tag", None) or getattr(pkg.device, "tag", "?")
        service = getattr(pkg, "service", "")
        ad = DeviceAdapter(tag, pkg, unit_code, service)
        reg[tag] = ad
        # The schedule's module names alias the same device: MC- for a
        # motor, XC- for an MOV or shutdown valve.
        base = tag.replace("-", "")
        prefix = "MC-" if ad.is_motor else "XC-"
        reg.setdefault(prefix + base, ad)
        for attr in ("run", "flt", "avl", "vfd_ok", "zso", "zsc", "trq",
                     "cmd", "cmd_start", "cmd_stop", "cmd_open", "cmd_close"):
            t = getattr(pkg, attr, None)
            if t is not None and hasattr(t, "name"):
                reg.setdefault(t.name, ad)

    from ..core.devices import Motor, MotorOperatedValve
    for unit in flowsheet.units:
        for obj in vars(unit).values():
            if isinstance(obj, (MotorPackage, MovPackage, SdvPackage)):
                add(obj, unit.code)
            elif isinstance(obj, PumpTrain):
                for sub in (obj.mov_a, obj.mov_b, obj.motor_a, obj.motor_b):
                    add(sub, unit.code)
            elif isinstance(obj, (Motor, MotorOperatedValve)):
                shim = _RawShim(obj, unit.db)
                if shim.complete() and shim.tag not in reg:
                    add(shim, unit.code)
    return reg


# ------------------------------------------------------------- status icons
class StatusIcon(QWidget):
    """The No-Permit, Interlocked and Bypassed icons of the DL_fp."""

    def __init__(self, kind: str, tip: str) -> None:
        super().__init__()
        self.kind = kind
        self.active = False
        self.setFixedSize(20, 20)
        self.setToolTip(tip)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c = QPointF(10, 10)
        if self.kind == "nopermit":
            col = QColor("#8C97A3" if not self.active else ALARM_RED)
            p.setPen(QPen(col, 2))
            p.drawEllipse(QRectF(3, 3, 14, 14))
            p.drawLine(QPointF(5.5, 14.5), QPointF(14.5, 5.5))
        elif self.kind == "interlock":
            col = QColor("#8C97A3" if not self.active else ALARM_RED)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            p.drawEllipse(QRectF(3, 3, 14, 14))
            p.setPen(QPen(QColor(WHITE), 1.6))
            p.setFont(_f(9, True))
            p.drawText(QRectF(3, 2, 14, 15), Qt.AlignCenter, "!")
        elif self.kind == "bypass":
            col = QColor(ADVISORY if self.active else "#B7BFC8")
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            p.drawPolygon(QPolygonF([QPointF(10, 2), QPointF(18, 10),
                                     QPointF(10, 18), QPointF(2, 10)]))
            p.setPen(QPen(QColor(WHITE), 1.8))
            p.drawLine(QPointF(6, 10), QPointF(14, 10))
        p.end()


class FirstOutArrow(QWidget):
    """The interlock first-out arrow: grey outline, solid red when active."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(16, 12)
        self.active = False

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        tri = QPolygonF([QPointF(3, 2), QPointF(12, 6), QPointF(3, 10)])
        if self.active:
            p.setPen(QPen(QColor(ALARM_RED), 1))
            p.setBrush(QBrush(QColor(ALARM_RED)))
        else:
            p.setPen(QPen(QColor("#B7BFC8"), 1.2))
            p.setBrush(Qt.NoBrush)
        p.drawPolygon(tri)
        p.end()


class DTrend(QWidget):
    """The DL_fp trend: the PV (speed or position) over the last minutes."""

    def __init__(self, adapter: DeviceAdapter) -> None:
        super().__init__()
        self.ad = adapter
        self.setFixedHeight(56)
        self._hist: List[float] = []

    def sample(self) -> None:
        self._hist.append(self.ad.pv_percent())
        if len(self._hist) > 300:
            self._hist = self._hist[-300:]

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(26, 3, self.width() - 32, self.height() - 8)
        p.setPen(QPen(QColor(DIM)))
        p.setFont(_f(7))
        p.drawText(QRectF(0, 0, 24, 11), Qt.AlignRight, "100")
        p.drawText(QRectF(0, self.height() - 13, 24, 11), Qt.AlignRight, "0")
        p.fillRect(r, QColor("#EDEFF2"))
        p.setPen(QPen(QColor(FRAME), 1))
        p.drawRect(r)
        if len(self._hist) >= 2:
            p.setPen(QPen(QColor(PV_BLUE), 1.2))
            n = len(self._hist)
            last = None
            for i, v in enumerate(self._hist):
                x = r.left() + r.width() * i / max(n - 1, 1)
                y = r.bottom() - max(0.0, min(1.0, v / 100.0)) * r.height()
                if last is not None:
                    p.drawLine(last, QPointF(x, y))
                last = QPointF(x, y)
        p.end()


# ============================================================== the faceplate
class DeviceFaceplate(LiveDialog):
    def __init__(self, adapter: DeviceAdapter, parent=None) -> None:
        super().__init__(parent)
        self.ad = adapter
        self.setWindowTitle(f"{adapter.tag}  faceplate")
        self.setFixedWidth(272)
        self.setStyleSheet(STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(3)

        root.addWidget(_lbl(adapter.service or adapter.kind, 7.5, colour=DIM,
                            align=Qt.AlignHCenter))
        root.addWidget(_lbl(adapter.tag, 13, True, align=Qt.AlignHCenter))
        root.addWidget(_lbl(f"{adapter.unit}", 7.5, colour=DIM,
                            align=Qt.AlignHCenter))

        # ----------------------- status icons | PV + buttons | S and modes
        mid = QGridLayout()
        mid.setHorizontalSpacing(6)

        icons_col = QVBoxLayout()
        self.ic_permit = StatusIcon("nopermit", "No permit")
        self.ic_lock = StatusIcon("interlock", "Interlocked")
        self.ic_byp = StatusIcon("bypass", "Bypassed")
        for w in (self.ic_permit, self.ic_lock, self.ic_byp):
            icons_col.addWidget(w)
        icons_col.addStretch(1)
        mid.addLayout(icons_col, 0, 0)

        centre = QVBoxLayout()
        pv_row = QHBoxLayout()
        self.lbl_state = _lbl("-", 13, True, PV_BLUE)
        pv_row.addWidget(self.lbl_state, 1)
        self.sim_badge = SimBadge()
        pv_row.addWidget(self.sim_badge, 0, Qt.AlignTop)
        pv_row.addWidget(Chevron(), 0, Qt.AlignTop)
        centre.addLayout(pv_row)

        self._state_btns: List[QPushButton] = []
        for label, cb in adapter.states():
            b = QPushButton(label)
            b.setFont(_f(9, True))
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, f=cb: self._command(f))
            centre.addWidget(b)
            self._state_btns.append(b)

        acc = QHBoxLayout()
        self.chk_accept = QCheckBox("Accept")
        self.chk_accept.setFont(_f(8))
        acc.addWidget(self.chk_accept)
        acc.addStretch(1)
        centre.addLayout(acc)
        mid.addLayout(centre, 0, 1)

        modes = QVBoxLayout()
        modes.addStretch(1)
        for text, big in (("Auto", False), ("Auto ▾", True)):
            row = QHBoxLayout()
            row.setSpacing(3)
            mark = _lbl("!", 7.5, True, WHITE, Qt.AlignCenter)
            mark.setFixedSize(10, 12)
            mark.setStyleSheet(f"background: {ADVISORY}; color: {WHITE};"
                               "border-radius: 2px;")
            row.addWidget(mark)
            lab = _lbl(text, 10 if big else 8.5, big,
                       TXT if big else "#9AA6B0")
            row.addWidget(lab, 1)
            modes.addLayout(row)
        mid.addLayout(modes, 0, 2)
        root.addLayout(mid)

        # ------------------------------------------------- state transition
        grid = QGridLayout()
        grid.setVerticalSpacing(1)
        grid.addWidget(_lbl("State Transition", 8.5, True), 0, 0, 1, 2)
        grid.addWidget(_lbl("Time Limit", 8), 1, 0)
        self.lbl_limit = _lbl("-", 8.5, colour=TXT, align=Qt.AlignRight)
        grid.addWidget(self.lbl_limit, 1, 1)
        grid.addWidget(_lbl("Elapsed Time", 8, colour=DIM), 2, 0)
        self.lbl_elapsed = _lbl("-", 8.5, colour=DIM, align=Qt.AlignRight)
        grid.addWidget(self.lbl_elapsed, 2, 1)

        grid.addWidget(_lbl("Device State", 8.5, True), 3, 0, 1, 2)
        state_row = QHBoxLayout()
        self.lbl_dstate = _lbl("-", 8.5)
        state_row.addWidget(self.lbl_dstate, 1)
        btn_reset = QPushButton("Reset")
        btn_reset.setFont(_f(8.5, True))
        btn_reset.setFixedSize(52, 20)
        btn_reset.clicked.connect(self._reset)
        state_row.addWidget(btn_reset)
        grid.addLayout(state_row, 4, 0, 1, 2)
        grid.addWidget(_lbl("Delayed", 8, colour=DIM), 5, 0)
        self.lbl_delay = _lbl("-", 8.5, colour=DIM, align=Qt.AlignRight)
        grid.addWidget(self.lbl_delay, 5, 1)

        grid.addWidget(_lbl("Fail Alarm", 8.5, True), 6, 0, 1, 2)
        self.lbl_fail = _lbl("", 8)
        grid.addWidget(self.lbl_fail, 7, 0, 1, 2)
        root.addLayout(grid)

        self.trend = DTrend(adapter)
        root.addWidget(self.trend)

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
            a.setStyleSheet(f"background: {PANEL_DK}; border: 1px solid {FRAME};")
            rail.addWidget(a)
        wrap.addLayout(rail)
        root.addLayout(wrap)

        unit_row = QHBoxLayout()
        unit_row.addWidget(_lbl("Unit:", 7.5, colour=DIM))
        unit_row.addWidget(_lbl(adapter.unit, 8, True))
        unit_row.addStretch(1)
        root.addLayout(unit_row)

        icons = QHBoxLayout()
        icons.setSpacing(6)
        icons.addWidget(IconButton("detail", "Device detail display",
                                   self._open_detail))
        icons.addWidget(IconButton("group", "Faceplate group", None, False))
        icons.addWidget(IconButton("sliders", "Interlocks", None, False))
        icons.addWidget(IconButton("list", "Module explorer", None, False))
        icons.addWidget(IconButton("bell", "Acknowledge alarms",
                                   self._ack))
        icons.addStretch(1)
        root.addLayout(icons)

        self._detail: Optional[DeviceDetail] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(400)
        self._refresh()

    def _ack(self) -> None:
        alarms = getattr(self.parent(), "alarms", None)
        if alarms is not None:
            alarms.ack_tag(self.ad.tag)

    # ------------------------------------------------------------ commands
    def _command(self, fn: Callable[[], None]) -> None:
        if not self.chk_accept.isChecked():
            self.alarm_area.setText("Check Accept, then the state button")
            return
        fn()
        self.chk_accept.setChecked(False)

    def _reset(self) -> None:
        self.ad.reset()

    def _open_detail(self) -> None:
        if self._detail is None:
            retain_detail(self, DeviceDetail(self.ad, self))
        else:
            self._detail.raise_()
        self._detail.show()

    # -------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        ad = self.ad
        self.lbl_state.setText(ad.state_text())
        failed = ad.failed()
        self.lbl_state.setStyleSheet(
            f"color: {ALARM_RED if failed else PV_BLUE};"
            "background: transparent;")
        self.sim_badge.active = ad.can_simulate and ad.pkg.simulate_enable
        self.sim_badge.update()

        limit, elapsed = ad.transition()
        self.lbl_limit.setText(f"{limit:.1f} s")
        self.lbl_elapsed.setText(f"{elapsed:.1f} s")
        self.lbl_dstate.setText(ad.state_text())
        self.lbl_delay.setText(f"{ad.delayed():.1f} s")
        fail = ad.fail_text()
        self.lbl_fail.setText(fail)
        self.lbl_fail.setStyleSheet(
            f"color: {ALARM_RED if fail else DIM}; background: transparent;")

        self.ic_lock.active = failed
        self.ic_lock.update()
        self.ic_permit.active = getattr(ad.pkg, "local", False)
        self.ic_permit.update()

        show = bool(fail) and self.ad.fail_enab
        self.alarm_pri.active = show
        self.alarm_pri.priority = self.ad.fail_priority
        self.alarm_pri.suppressed = self.ad.fail_shelved
        self.alarm_pri.update()
        self.alarm_area.setText(fail if show else "")
        self.trend.sample()
        self.trend.update()


# ================================================================ the detail
class DeviceDetail(LiveDialog):
    def __init__(self, adapter: DeviceAdapter, parent=None) -> None:
        super().__init__(parent)
        self.ad = adapter
        self.setWindowTitle(f"{adapter.tag}  device detail")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)

        head = QGridLayout()
        head.addWidget(_lbl(adapter.service, 7.5, colour=DIM,
                            align=Qt.AlignHCenter), 0, 1)
        head.addWidget(_lbl(adapter.tag, 13, True, align=Qt.AlignHCenter), 1, 1)
        head.addWidget(_lbl(adapter.unit, 7.5, colour=DIM,
                            align=Qt.AlignHCenter), 2, 1)
        self.alarm_icon = AlarmIcon()
        head.addWidget(self.alarm_icon, 0, 2, 2, 1, Qt.AlignRight)
        head.addWidget(IconButton("group", "Faceplate",
                                  lambda: parent.raise_() if parent else None),
                       0, 3, 2, 1, Qt.AlignRight)
        head.setColumnStretch(1, 1)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ------------------------------------------------------ left column
        left = QVBoxLayout()
        left.addWidget(_lbl("Limits", 9, True))
        left.addWidget(_lbl("Confirm Time", 8, colour=DIM))
        lg = QGridLayout()
        lg.setVerticalSpacing(2)
        pkg = adapter.pkg

        def bind_row(r, label, obj, attr, unit="s"):
            lg.addWidget(_lbl(label, 8), r, 0)
            if obj is not None and hasattr(obj, attr):
                e = _field(f"{getattr(obj, attr):g}", 62)
                e.editingFinished.connect(
                    lambda o=obj, a=attr, ed=e: self._setf(o, a, ed))
            else:
                e = _field("0", 62)
                e.setEnabled(False)
                e.setToolTip("Not modelled for this device")
            lg.addWidget(e, r, 1)
            lg.addWidget(_lbl(unit, 7.5, colour=DIM), r, 2)

        dev = getattr(pkg, "device", None)
        bind_row(0, "  Passive", None, "")
        bind_row(1, "  Active1", None, "")
        bind_row(2, "  Active2", None, "")
        bind_row(3, "Trip Time", None, "")
        bind_row(4, "Delay Time", dev if adapter.is_motor else None,
                 "start_delay")
        bind_row(5, "Restart Time", dev if adapter.is_motor else None,
                 "coast_time")
        bind_row(6, "Crack Time",
                 dev if adapter.is_mov else (pkg if not adapter.is_motor
                                             else None),
                 "travel_time" if adapter.is_mov else "stroke")
        left.addLayout(lg)

        left.addSpacing(6)
        left.addWidget(_lbl("Simulate", 9, True))
        srow = QGridLayout()
        self.chk_sim = QCheckBox("Simulate")
        self.chk_sim.setFont(_f(8))
        self.chk_sim.setEnabled(adapter.can_simulate)
        if adapter.can_simulate:
            self.chk_sim.setChecked(pkg.simulate_enable)
            self.chk_sim.toggled.connect(
                lambda v: setattr(pkg, "simulate_enable", bool(v)))
        srow.addWidget(self.chk_sim, 0, 0)
        self.ed_sim = _field("0", 52)
        self.ed_sim.setEnabled(adapter.can_simulate)
        if adapter.can_simulate:
            self.ed_sim.setText(f"{pkg.simulate_value:g}")
            self.ed_sim.editingFinished.connect(self._simv)
        srow.addWidget(self.ed_sim, 0, 1)
        left.addLayout(srow)

        left.addSpacing(6)
        left.addWidget(_lbl("I/O", 9, True))
        io = QGridLayout()
        io.setVerticalSpacing(1)
        io.addWidget(_lbl("Field Value", 8), 0, 0)
        self.lbl_fv = _lbl("-", 8.5, colour=PV_BLUE)
        io.addWidget(self.lbl_fv, 0, 1, 1, 4)
        io.addWidget(_lbl("Output Value", 8), 1, 0)
        self.lbl_ov = _lbl("-", 8.5, colour=OUT_TEAL)
        io.addWidget(self.lbl_ov, 1, 1, 1, 4)
        ins, outs = adapter.io()
        self._io_ins: List[QLabel] = []
        self._io_outs: List[QLabel] = []
        for c, (name, _v) in enumerate(ins):
            io.addWidget(_lbl(name, 6.5, colour=DIM, align=Qt.AlignHCenter),
                         2, 1 + c)
        io.addWidget(_lbl("Inputs", 8), 3, 0)
        for c, (_n, v) in enumerate(ins):
            l = _lbl(v, 8.5, colour=OUT_TEAL, align=Qt.AlignHCenter)
            self._io_ins.append(l)
            io.addWidget(l, 3, 1 + c)
        io.addWidget(_lbl("Outputs", 8), 4, 0)
        for c, (_n, v) in enumerate(outs):
            l = _lbl(v, 8.5, colour=OUT_TEAL, align=Qt.AlignHCenter)
            self._io_outs.append(l)
            io.addWidget(l, 4, 1 + c)
        left.addLayout(io)
        left.addStretch(1)
        body.addLayout(left)

        # ----------------------------------------------------- right column
        right = QVBoxLayout()
        arow = QGridLayout()
        arow.addWidget(_lbl("Alarms", 9, True), 0, 0)
        for c, cap in enumerate(("Enab", "OOS", "Shlv", "Help")):
            arow.addWidget(_lbl(cap, 7, colour=DIM, align=Qt.AlignHCenter),
                           0, 2 + c)
        arow.addWidget(_lbl("Fail Alarm", 8), 1, 0)
        cb = QComboBox()
        cb.setFont(_f(7.5))
        cb.addItems(["CRITICAL", "WARNING", "ADVISORY"])
        cb.setCurrentText(adapter.fail_priority)
        cb.currentTextChanged.connect(
            lambda s: setattr(self.ad, "fail_priority", s))
        cb.setFixedWidth(88)
        arow.addWidget(cb, 1, 1)
        for c, attr in enumerate(("fail_enab", None, "fail_shelved")):
            k = QCheckBox()
            k.setChecked(c == 0)
            if attr is None:
                k.setEnabled(False)
            else:
                k.toggled.connect(
                    lambda v, a=attr: setattr(self.ad, a, bool(v)))
            arow.addWidget(k, 1, 2 + c, Qt.AlignHCenter)
        h = _lbl("?", 8, True, WHITE, Qt.AlignCenter)
        h.setFixedSize(13, 13)
        h.setStyleSheet(f"background: {ADVISORY}; color: {WHITE};"
                        "border-radius: 6px;")
        arow.addWidget(h, 1, 5, Qt.AlignHCenter)
        arow.addWidget(_lbl("Priority Adj", 8), 2, 0)
        adj = QComboBox()
        adj.setFont(_f(8))
        adj.addItems(["0", "+1", "-1"])
        adj.setFixedWidth(46)
        arow.addWidget(adj, 2, 1)
        right.addLayout(arow)

        right.addWidget(_lbl("Diagnostics", 9, True))
        self.tabs = QTabWidget()
        self.tabs.setFixedHeight(110)
        self.diag = QLabel("Module OK")
        self.diag.setFont(_f(8.5))
        self.diag.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.diag.setMargin(6)
        self.diag.setStyleSheet(f"background: {WHITE};")
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(4, 4, 4, 4)
        btn = QPushButton("Clear Error")
        btn.setFont(_f(8))
        btn.clicked.connect(self.ad.reset)
        wl.addWidget(self.diag, 1)
        wl.addWidget(btn, 0, Qt.AlignRight)
        self.tabs.addTab(wrap, "MERROR")
        for name in ("MSTATUS", "BLOCKERR"):
            pg = QLabel("No entries")
            pg.setFont(_f(8.5))
            pg.setMargin(6)
            pg.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.tabs.addTab(pg, name)
        right.addWidget(self.tabs)

        ilk_hdr = QHBoxLayout()
        ilk_hdr.addWidget(_lbl("Interlocks", 9, True))
        rst = QPushButton("Reset")
        rst.setFont(_f(8, True))
        rst.setFixedSize(52, 20)
        rst.clicked.connect(self.ad.reset)
        ilk_hdr.addWidget(rst)
        ilk_hdr.addStretch(1)
        ilk_hdr.addWidget(_lbl("Bypass", 7.5, colour=DIM))
        right.addLayout(ilk_hdr)
        right.addWidget(_lbl("First Out   Condition", 7.5, colour=DIM))
        self._ilk_rows: List[Tuple[FirstOutArrow, QLabel, str]] = []
        for text, _active in adapter.interlocks():
            row = QHBoxLayout()
            arrow = FirstOutArrow()
            row.addWidget(arrow, 0, Qt.AlignVCenter)
            lab = _lbl(text, 8)
            row.addWidget(lab, 1)
            byp = QCheckBox()
            byp.setEnabled(False)
            byp.setToolTip("Interlock bypass is not permitted here")
            row.addWidget(byp)
            right.addLayout(row)
            self._ilk_rows.append((arrow, lab, text))
        right.addStretch(1)
        body.addLayout(right)
        outer.addLayout(body)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(600)
        self._refresh()

    def _setf(self, obj, attr, ed) -> None:
        try:
            setattr(obj, attr, float(ed.text()))
        except ValueError:
            ed.setText(f"{getattr(obj, attr):g}")

    def _simv(self) -> None:
        try:
            self.ad.pkg.simulate_value = float(self.ed_sim.text())
        except ValueError:
            pass

    def _refresh(self) -> None:
        ad = self.ad
        self.lbl_fv.setText(f"{ad.field_value()}  {ad.state_text()}")
        self.lbl_ov.setText(f"{ad.pv_percent():.0f}")
        ins, outs = ad.io()
        for l, (_n, v) in zip(self._io_ins, ins):
            l.setText(v)
        for l, (_n, v) in zip(self._io_outs, outs):
            l.setText(v)
        fail = ad.fail_text()
        self.alarm_icon.active = bool(fail) and ad.fail_enab
        self.alarm_icon.priority = ad.fail_priority
        self.alarm_icon.suppressed = ad.fail_shelved
        self.alarm_icon.update()
        self.diag.setText(fail if fail else "Module OK")
        self.diag.setStyleSheet(
            f"background: {WHITE}; color: {ALARM_RED if fail else TXT};")
        for arrow, _lab, text in self._ilk_rows:
            arrow.active = dict(ad.interlocks()).get(text, False)
            arrow.update()
