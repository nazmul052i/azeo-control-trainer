"""The Tuning Lab: step test, model identification, recommendations.

The practice loop the reference package teaches: put a loop in MANUAL,
step the output, watch the response, identify a process model, and get
defensible settings out of it. Everything runs against the live plant
through the same faceplate API an operator uses - set_mode and the
output - and the x-axis is simulation time, so tests are honest at any
speed factor.

Identification: self-regulating processes fit first-order-plus-deadtime
by the classic two-point method (28.3% / 63.2% response times);
integrating processes (levels) fit ramp rate and deadtime. The fitted
model is drawn over the recorded response so the quality of the fit is
visible, not asserted.

Recommendations: SIMC (lambda tuning, the lambda-to-deadtime ratio is
yours to choose) and open-loop Ziegler-Nichols, both as PI, applied to
the block with one click and recorded in the journal.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from ..control.pid import Mode
from . import theme

PV_BLUE = QColor("#2E5F9E")
OUT_TEAL = QColor("#0E6B5C")
MODEL_RED = QColor("#C82020")
GRID_C = QColor("#D5DAE0")


class _TestChart(QWidget):
    """PV and OUT against test time, with the fitted model overlaid."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(240)
        self.t: List[float] = []
        self.pv: List[float] = []
        self.out: List[float] = []
        self.t0: Optional[float] = None      # step instant
        self.model: List[Tuple[float, float]] = []

    def clear(self) -> None:
        self.t, self.pv, self.out, self.model = [], [], [], []
        self.t0 = None
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        ml, mr, mt, mb = 44, 14, 10, 26
        plot = QRectF(ml, mt, w - ml - mr, h - mt - mb)
        p.setPen(QPen(GRID_C, 1))
        for i in range(1, 5):
            y = plot.top() + plot.height() * i / 5
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(QPen(QColor("#9AA3AD"), 1))
        p.drawRect(plot)
        if len(self.t) < 2:
            p.setPen(QPen(QColor(theme.MUTED_TEXT)))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(plot, Qt.AlignCenter,
                       "Select a loop and start a step test")
            p.end()
            return
        tmin, tmax = self.t[0], max(self.t[-1], self.t[0] + 1.0)
        # the PV axis follows the response so a slow level test is
        # readable; OUT keeps the full 0-100 scale
        vall = self.pv + [v for _t, v in self.model]
        vmin, vmax = min(vall), max(vall)
        pad = max((vmax - vmin) * 0.15, 2.0)
        vmin, vmax = vmin - pad, vmax + pad

        def x_of(tv: float) -> float:
            return plot.left() + (tv - tmin) / (tmax - tmin) \
                * plot.width()

        def xy(tv: float, v: float) -> QPointF:
            y = plot.bottom() - (v - vmin) / (vmax - vmin) \
                * plot.height()
            return QPointF(x_of(tv), y)

        def xy_out(tv: float, v: float) -> QPointF:
            y = plot.bottom() - max(0.0, min(100.0, v)) / 100.0 \
                * plot.height()
            return QPointF(x_of(tv), y)

        if self.t0 is not None and tmin <= self.t0 <= tmax:
            p.setPen(QPen(QColor("#B9C1CA"), 1, Qt.DashLine))
            x0 = x_of(self.t0)
            p.drawLine(QPointF(x0, plot.top()), QPointF(x0, plot.bottom()))
        poly = QPolygonF([xy_out(tv, v)
                          for tv, v in zip(self.t, self.out)])
        p.setPen(QPen(OUT_TEAL, 1.4))
        p.drawPolyline(poly)
        poly = QPolygonF([xy(tv, v) for tv, v in zip(self.t, self.pv)])
        p.setPen(QPen(PV_BLUE, 1.8))
        p.drawPolyline(poly)
        if self.model:
            poly = QPolygonF([xy(tv, v) for tv, v in self.model])
            pen = QPen(MODEL_RED, 1.6, Qt.DashLine)
            p.setPen(pen)
            p.drawPolyline(poly)
        p.setPen(QPen(QColor(theme.MUTED_TEXT)))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(QRectF(2, plot.top() - 4, ml - 6, 12),
                   Qt.AlignRight, f"{vmax:.1f}%")
        p.drawText(QRectF(2, plot.bottom() - 8, ml - 6, 12),
                   Qt.AlignRight, f"{vmin:.1f}%")
        p.drawText(QRectF(plot.left(), h - 18, plot.width(), 14),
                   Qt.AlignHCenter,
                   f"test time  ·  {tmax - tmin:.0f} s")
        legend = [("PV", PV_BLUE), ("OUT", OUT_TEAL),
                  ("model", MODEL_RED)]
        x = plot.right() - 170
        for text, colour in legend:
            p.setPen(QPen(colour, 2))
            p.drawLine(QPointF(x, mt + 8), QPointF(x + 16, mt + 8))
            p.setPen(QPen(QColor(theme.NORMAL_TEXT)))
            p.drawText(QPointF(x + 20, mt + 12), text)
            x += 60
        p.end()


class TuningLab(QWidget):
    """Content widget; open through the FloatingShell."""

    def __init__(self, engine, controller, journal=None) -> None:
        super().__init__()
        self.engine = engine
        self.controller = controller
        self.journal = journal
        self.phase = "IDLE"
        self._pid = None
        self._module = ""
        self._restore = None          # (target_mode, out)
        self._t_phase = 0.0
        self._pv0 = 0.0
        self._noise = 0.1
        self._du = 0.0
        self._fit = None              # dict with model + recommendations

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(QLabel("Loop"))
        self.cmb_loop = QComboBox()
        loops = sorted(m for m, lp in controller.loops.items()
                       if lp.out_tag)
        self.cmb_loop.addItems(loops)
        self.cmb_loop.setMinimumWidth(110)
        row.addWidget(self.cmb_loop)
        row.addSpacing(10)
        row.addWidget(QLabel("Step"))
        self.sp_step = QDoubleSpinBox()
        self.sp_step.setRange(-25.0, 25.0)
        self.sp_step.setValue(5.0)
        self.sp_step.setSuffix(" %")
        row.addWidget(self.sp_step)
        row.addWidget(QLabel("Baseline"))
        self.sp_base = QDoubleSpinBox()
        self.sp_base.setRange(5.0, 300.0)
        self.sp_base.setValue(20.0)
        self.sp_base.setSuffix(" s")
        row.addWidget(self.sp_base)
        row.addWidget(QLabel("Max test"))
        self.sp_max = QDoubleSpinBox()
        self.sp_max.setRange(30.0, 3600.0)
        self.sp_max.setValue(600.0)
        self.sp_max.setSuffix(" s")
        row.addWidget(self.sp_max)
        row.addWidget(QLabel("Type"))
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(["Auto", "Self-regulating", "Integrating"])
        row.addWidget(self.cmb_type)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_start = QPushButton("Start step test")
        self.btn_start.clicked.connect(self._start)
        self.btn_abort = QPushButton("Abort && restore")
        self.btn_abort.clicked.connect(self._abort)
        self.btn_abort.setEnabled(False)
        self.chk_restore = QCheckBox("Restore output and mode after "
                                     "the test")
        self.chk_restore.setChecked(True)
        row2.addWidget(self.btn_start)
        row2.addWidget(self.btn_abort)
        row2.addWidget(self.chk_restore)
        row2.addSpacing(16)
        row2.addWidget(QLabel("λ = "))
        self.sp_lam = QDoubleSpinBox()
        self.sp_lam.setRange(0.5, 10.0)
        self.sp_lam.setSingleStep(0.5)
        self.sp_lam.setValue(3.0)
        self.sp_lam.valueChanged.connect(self._recompute)
        row2.addWidget(self.sp_lam)
        row2.addWidget(QLabel("× θ  (closed-loop speed; larger is "
                              "gentler)"))
        row2.addStretch(1)
        lay.addLayout(row2)

        self.status = QLabel("Idle.")
        self.status.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")
        lay.addWidget(self.status)

        self.chart = _TestChart()
        lay.addWidget(self.chart, 1)

        # ------------------------------------------------- results table
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        heads = ["", "Process model", "Current", "SIMC (λ)",
                 "Ziegler-Nichols"]
        for c, htext in enumerate(heads):
            lb = QLabel(htext)
            lb.setStyleSheet("font-weight: 600;")
            grid.addWidget(lb, 0, c)
        self.cells = {}
        rows = [("model", "Kp / τ / θ"), ("gain", "Gain"),
                ("reset", "Reset (s)")]
        for r, (key, caption) in enumerate(rows, start=1):
            grid.addWidget(QLabel(caption), r, 0)
            for c, col in enumerate(("modelv", "cur", "simc", "zn"),
                                    start=1):
                lb = QLabel("-")
                self.cells[(key, col)] = lb
                grid.addWidget(lb, r, c)
        self.btn_apply_simc = QPushButton("Apply SIMC")
        self.btn_apply_zn = QPushButton("Apply ZN")
        self.btn_apply_simc.clicked.connect(lambda: self._apply("simc"))
        self.btn_apply_zn.clicked.connect(lambda: self._apply("zn"))
        for b in (self.btn_apply_simc, self.btn_apply_zn):
            b.setEnabled(False)
        grid.addWidget(self.btn_apply_simc, 4, 3)
        grid.addWidget(self.btn_apply_zn, 4, 4)
        lay.addLayout(grid)

    # ------------------------------------------------------------- machine
    def _sim_t(self) -> float:
        return float(self.engine.stats.sim_time)

    def _say(self, text: str, colour=None) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color: {(colour or theme.MUTED_TEXT).name()};")

    def _start(self) -> None:
        module = self.cmb_loop.currentText()
        loop = self.controller.loops.get(module)
        if loop is None:
            return
        pid = loop.pid
        self._module, self._pid = module, pid
        self._restore = (pid.target_mode, float(pid.out))
        pid.set_mode(Mode.MAN)
        self.chart.clear()
        self._fit = None
        self._refresh_table()
        self.phase = "BASELINE"
        self._t_phase = self._sim_t()
        self.btn_start.setEnabled(False)
        self.btn_abort.setEnabled(True)
        self._say(f"{module}: in MANUAL, recording the baseline...")
        if self.journal is not None:
            self.journal.log("OPERATOR",
                             f"Tuning Lab step test started on {module}",
                             source="tuning", tag=module)

    def _abort(self) -> None:
        self._finish(aborted=True)

    def _finish(self, aborted: bool = False) -> None:
        pid = self._pid
        if pid is not None and self._restore is not None \
                and (aborted or self.chk_restore.isChecked()):
            pid.out = self._restore[1]
            pid.set_mode(self._restore[0])
        self.phase = "IDLE"
        self.btn_start.setEnabled(True)
        self.btn_abort.setEnabled(False)
        if aborted:
            self._say("Test aborted; output and mode restored.",
                      theme.ALARM_HIGH)
            return
        self._identify()

    def refresh(self, _snap=None) -> None:
        if self.phase == "IDLE" or self._pid is None:
            return
        pid = self._pid
        t = self._sim_t()
        self.chart.t.append(t)
        self.chart.pv.append(pid._pct(pid.pv))
        self.chart.out.append(float(pid.out))
        self.chart.update()
        if self.phase == "BASELINE":
            if t - self._t_phase >= self.sp_base.value():
                base = [v for tv, v in zip(self.chart.t, self.chart.pv)
                        if tv >= self._t_phase]
                self._pv0 = sum(base) / len(base)
                self._noise = max(
                    (max(base) - min(base)) / 2.0, 0.02)
                self._du = self.sp_step.value()
                new_out = max(0.0, min(100.0,
                                       float(pid.out) + self._du))
                self._du = new_out - float(pid.out)
                if abs(self._du) < 0.5:
                    self._say("Output is against a limit; step the "
                              "other way.", theme.ALARM_HIGH)
                    self._finish(aborted=True)
                    return
                pid.out = new_out
                self.chart.t0 = t
                self.phase = "STEP"
                self._t_phase = t
                self._say(f"Stepped OUT by {self._du:+.1f}%; "
                          "recording the response...")
            return
        # STEP phase
        elapsed = t - self._t_phase
        pv_now = self.chart.pv[-1]
        if pv_now <= 1.0 or pv_now >= 99.0:
            self._say("PV reached the end of range; test stopped and "
                      "restored.", theme.ALARM_HIGH)
            self._finish(aborted=True)
            return
        if elapsed >= self.sp_max.value():
            self._finish()
            return
        if elapsed >= 30.0 and self._settled(t):
            self._finish()

    def _window(self, t_from: float, t_to: float) -> List[float]:
        return [v for tv, v in zip(self.chart.t, self.chart.pv)
                if t_from <= tv <= t_to]

    def _settled(self, t: float) -> bool:
        span = max(t - self._t_phase, 1.0)
        w = max(span * 0.2, 8.0)
        last = self._window(t - w, t)
        prev = self._window(t - 2 * w, t - w)
        if len(last) < 4 or len(prev) < 4:
            return False
        move = abs(sum(last) / len(last) - sum(prev) / len(prev))
        return move < max(2.0 * self._noise, 0.15)

    # ------------------------------------------------------ identification
    def _step_data(self):
        t0 = self.chart.t0
        pts = [(tv - t0, v) for tv, v in zip(self.chart.t, self.chart.pv)
               if tv >= t0]
        return t0, pts

    def _identify(self) -> None:
        t0, pts = self._step_data()
        if t0 is None or len(pts) < 8 or abs(self._du) < 0.5:
            self._say("Not enough data to identify a model.",
                      theme.ALARM_HIGH)
            return
        kind = self.cmb_type.currentText()
        if kind == "Auto":
            kind = ("Integrating" if self._looks_integrating(pts)
                    else "Self-regulating")
        fit = (self._fit_integrating(pts) if kind == "Integrating"
               else self._fit_foptd(pts))
        if fit is None:
            self._say("Identification failed - the response never "
                      "developed. Try a larger step or longer test.",
                      theme.ALARM_HIGH)
            return
        fit["kind"] = kind
        self._fit = fit
        self._draw_model()
        self._recompute()
        sgn = " (direct-acting)" if fit["kp"] < 0 else ""
        if kind == "Integrating":
            self._say(f"{self._module}: integrating fit  Kp' = "
                      f"{fit['kp']:.4f} %/%/s, θ = {fit['theta']:.1f} s"
                      + sgn, theme.RUNNING)
        else:
            self._say(f"{self._module}: FOPDT fit  Kp = {fit['kp']:.3f}"
                      f" %/%, τ = {fit['tau']:.1f} s, θ = "
                      f"{fit['theta']:.1f} s" + sgn, theme.RUNNING)

    def _looks_integrating(self, pts) -> bool:
        n = len(pts)
        tail = pts[int(n * 0.7):]
        head = pts[int(n * 0.25):int(n * 0.55)]
        if len(tail) < 3 or len(head) < 3:
            return False

        def slope(seg):
            t_m = sum(p[0] for p in seg) / len(seg)
            v_m = sum(p[1] for p in seg) / len(seg)
            num = sum((p[0] - t_m) * (p[1] - v_m) for p in seg)
            den = sum((p[0] - t_m) ** 2 for p in seg) or 1e-9
            return num / den

        s_tail, s_head = slope(tail), slope(head)
        return abs(s_tail) > 0.4 * abs(s_head) and abs(s_tail) > 1e-4

    def _fit_foptd(self, pts):
        pv_f = sum(v for _t, v in pts[int(len(pts) * 0.85):]) \
            / max(len(pts[int(len(pts) * 0.85):]), 1)
        dpv = pv_f - self._pv0
        if abs(dpv) < max(3.0 * self._noise, 0.2):
            return None
        kp = dpv / self._du

        def crossing(frac: float) -> Optional[float]:
            target = self._pv0 + frac * dpv
            for (t1, v1), (t2, v2) in zip(pts, pts[1:]):
                if (v1 - target) * (v2 - target) <= 0 and v1 != v2:
                    return t1 + (target - v1) / (v2 - v1) * (t2 - t1)
            return None

        t28, t63 = crossing(0.283), crossing(0.632)
        if t28 is None or t63 is None or t63 <= t28:
            return None
        tau = 1.5 * (t63 - t28)
        theta = max(t63 - tau, 0.0)
        return {"kp": kp, "tau": tau, "theta": theta,
                "pv0": self._pv0, "pv_f": pv_f}

    def _fit_integrating(self, pts):
        seg = pts[int(len(pts) * 0.35):]
        if len(seg) < 4:
            return None
        t_m = sum(p[0] for p in seg) / len(seg)
        v_m = sum(p[1] for p in seg) / len(seg)
        num = sum((p[0] - t_m) * (p[1] - v_m) for p in seg)
        den = sum((p[0] - t_m) ** 2 for p in seg) or 1e-9
        slope = num / den
        if abs(slope) < 1e-5:
            return None
        kp = slope / self._du                     # %span / %out / s
        theta = max(t_m - (v_m - self._pv0) / slope, 0.0)
        return {"kp": kp, "tau": float("inf"), "theta": theta,
                "pv0": self._pv0, "slope": slope}

    def _draw_model(self) -> None:
        fit, t0 = self._fit, self.chart.t0
        if fit is None or t0 is None:
            return
        t_end = self.chart.t[-1]
        pts = []
        n = 120
        for i in range(n + 1):
            tr = (t_end - t0) * i / n
            if fit["kind"] == "Integrating":
                v = fit["pv0"] + (0.0 if tr < fit["theta"] else
                                  fit["slope"] * (tr - fit["theta"]))
            else:
                dpv = (fit["pv_f"] - fit["pv0"])
                v = fit["pv0"] + (0.0 if tr < fit["theta"] else
                                  dpv * (1.0 - math.exp(
                                      -(tr - fit["theta"])
                                      / max(fit["tau"], 1e-6))))
            pts.append((t0 + tr, v))
        self.chart.model = pts
        self.chart.update()

    # ------------------------------------------------------ recommendations
    def _recompute(self) -> None:
        self._refresh_table()

    def _recommend(self):
        fit = self._fit
        if fit is None:
            return None
        kp, theta = abs(fit["kp"]), fit["theta"]
        lam_k = self.sp_lam.value()
        if fit["kind"] == "Integrating":
            lam = max(lam_k * theta, 10.0)
            simc = (1.0 / (kp * (lam + theta)), 4.0 * (lam + theta))
            zn = None                  # open-loop ZN needs a settling gain
        else:
            tau = fit["tau"]
            lam = max(lam_k * theta, 0.2 * tau, 2.0)
            simc = (tau / (kp * (lam + theta)),
                    min(tau, 4.0 * (lam + theta)))
            th = max(theta, 1.0)
            zn = (0.9 * tau / (kp * th), 3.33 * th)
        return {"simc": simc, "zn": zn, "lam": lam}

    def _refresh_table(self) -> None:
        fit = self._fit
        rec = self._recommend()
        pid = self._pid

        def put(key, col, text):
            self.cells[(key, col)].setText(text)

        if fit is None:
            for key in ("model", "gain", "reset"):
                for col in ("modelv", "simc", "zn"):
                    put(key, col, "-")
        else:
            if fit["kind"] == "Integrating":
                put("model", "modelv",
                    f"Kp' {fit['kp']:.4f}/s · θ {fit['theta']:.1f} s")
            else:
                put("model", "modelv",
                    f"Kp {fit['kp']:.3f} · τ {fit['tau']:.1f} s · "
                    f"θ {fit['theta']:.1f} s")
            put("model", "simc", f"λ = {rec['lam']:.1f} s")
            put("model", "zn", "-" if rec["zn"] is None else "open loop")
            put("gain", "simc", f"{rec['simc'][0]:.3f}")
            put("reset", "simc", f"{rec['simc'][1]:.0f}")
            put("gain", "zn",
                "-" if rec["zn"] is None else f"{rec['zn'][0]:.3f}")
            put("reset", "zn",
                "-" if rec["zn"] is None else f"{rec['zn'][1]:.0f}")
        put("model", "cur", self._module or "-")
        put("gain", "cur", f"{pid.gain:.3f}" if pid else "-")
        put("reset", "cur", f"{pid.reset:.0f}" if pid else "-")
        self.btn_apply_simc.setEnabled(rec is not None)
        self.btn_apply_zn.setEnabled(
            rec is not None and rec["zn"] is not None)

    def _apply(self, which: str) -> None:
        rec = self._recommend()
        pid = self._pid
        if rec is None or pid is None:
            return
        gain, reset = rec[which] if which == "simc" else rec["zn"]
        pid.gain = float(gain)
        pid.reset = float(reset)
        self._refresh_table()
        self._say(f"{self._module}: applied {which.upper()}  gain "
                  f"{gain:.3f}, reset {reset:.0f} s.", theme.RUNNING)
        if self.journal is not None:
            self.journal.log(
                "OPERATOR",
                f"Tuning Lab applied {which.upper()} to "
                f"{self._module}: gain {gain:.3f}, reset {reset:.0f} s",
                source="tuning", tag=self._module)
