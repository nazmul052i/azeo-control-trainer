"""Steady-state column solver - the reference's design tool, on our
own column law.

Given feed rate, composition, temperature and pressure, plus any two
specifications (product qualities, or upper/lower tray temperatures
inverted through the bubble line), it solves the SAME relationships
the dynamic model integrates - overall and component balance,
separation from alpha^(N.eta.0.55), tray efficiency from the reflux
ratio, feed quality from subcooling - for distillate, bottoms, reflux
and reboiler steam, and draws the resulting composition profile.
T1 and T2 come pre-loaded from their model constants; the User column
is yours to design.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

from ..models.u500_u600_columns import ColumnT1, ColumnT2
from . import theme

STEAM_M3_PER_TPH = 10.3      # boilup per t/h steam, from the model's latent


class _Plot(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(260, 300)
        self.points = []

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)
        ml, mr, mt, mb = 34, 10, 22, 26
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawLine(ml, mt, ml, h - mb)
        p.drawLine(ml, h - mb, w - mr, h - mb)
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(ml, mt - 6, "light key vs stage (top down)")
        p.drawText(ml - 26, mt + 8, "1.0")
        p.drawText(ml - 26, h - mb, "0.0")
        if not self.points:
            return
        poly = QPolygonF()
        for f, x in self.points:
            poly.append(QPointF(ml + x * (w - ml - mr),
                                mt + f * (h - mt - mb)))
        p.setPen(QPen(theme.PROCESS_LINE, 2))
        p.drawPolyline(poly)


class ColumnSolver(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Column solver  ·  AzeoPlant")
        self.resize(780, 520)

        self.col = QComboBox()
        self.col.addItems(["T1", "T2", "User"])
        self.col.currentTextChanged.connect(self._load_col)

        def ed(v):
            e = QLineEdit(str(v))
            e.setMaximumWidth(90)
            return e

        self.feed, self.zf = ed(78.0), ed(0.46)
        self.tf, self.pcol = ed(150.0), ed(8.5)
        self.stages, self.alpha = ed(46), ed(2.6)
        self.t_light, self.t_heavy = ed(118.0), ed(268.0)

        self.spec_q = QRadioButton("product qualities")
        self.spec_t = QRadioButton("tray temperatures")
        self.spec_q.setChecked(True)
        self.s1, self.s2 = ed(1.0), ed(5.0)
        self.spec_lbl = QLabel("heavy in dist %  /  light in btms %")
        self.spec_q.toggled.connect(self._spec_mode)

        form = QFormLayout()
        form.addRow("column", self.col)
        form.addRow("feed  m3/h", self.feed)
        form.addRow("light in feed  frac", self.zf)
        form.addRow("feed temperature  degC", self.tf)
        form.addRow("pressure  barg", self.pcol)
        form.addRow("stages", self.stages)
        form.addRow("alpha", self.alpha)
        form.addRow("T light / T heavy", self._pair(self.t_light,
                                                    self.t_heavy))
        form.addRow(self.spec_q, self.spec_t)
        form.addRow(self.spec_lbl, self._pair(self.s1, self.s2))

        solve = QPushButton("Solve")
        solve.clicked.connect(self._solve)
        self.result = QLabel("-")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(solve)
        left.addWidget(self.result)
        left.addStretch(1)

        self.plot = _Plot()
        lay = QHBoxLayout(self)
        box = QWidget()
        box.setLayout(left)
        box.setMaximumWidth(360)
        lay.addWidget(box)
        lay.addWidget(self.plot, 1)
        self._load_col("T1")

    @staticmethod
    def _pair(a, b) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(b)
        return w

    def _spec_mode(self) -> None:
        if self.spec_q.isChecked():
            self.spec_lbl.setText("heavy in dist %  /  light in btms %")
            self.s1.setText("1.0")
            self.s2.setText("5.0")
        else:
            self.spec_lbl.setText("upper tray degC  /  lower tray degC")
            self.s1.setText("133.0")
            self.s2.setText("225.0")

    def _load_col(self, name: str) -> None:
        cls = {"T1": ColumnT1, "T2": ColumnT2}.get(name)
        editable = cls is None
        for e in (self.stages, self.alpha, self.t_light, self.t_heavy):
            e.setReadOnly(not editable)
        if cls is not None:
            self.stages.setText(str(cls.STAGES))
            self.alpha.setText(str(cls.ALPHA))
            self.t_light.setText(str(cls.T_LIGHT))
            self.t_heavy.setText(str(cls.T_HEAVY))
            self.pcol.setText(str(cls.P_NOMINAL))

    def _solve(self) -> None:
        try:
            F = float(self.feed.text())
            z = float(self.zf.text())
            tf = float(self.tf.text())
            p = float(self.pcol.text())
            n_st = max(int(float(self.stages.text())), 3)
            alpha = max(float(self.alpha.text()), 1.05)
            tl = float(self.t_light.text())
            th = float(self.t_heavy.text())
            s1 = float(self.s1.text())
            s2 = float(self.s2.text())
        except ValueError:
            self.result.setText("bad input")
            return
        p_corr = 0.0
        if self.spec_t.isChecked():
            # invert the bubble line for the section compositions the
            # model's tray transmitters stand at (see tray temps in the
            # column model), then unwrap to product compositions
            x_up = (th + p_corr - s1) / max(th - tl, 1e-6)
            x_lo = (th + p_corr - s2) / max(th - tl, 1e-6)
            xd = min(max(2.0 * x_up - 0.85, 0.02), 0.999)
            xb = min(max(2.0 * x_lo - 0.25, 0.001), 0.98)
        else:
            xd = min(max(1.0 - s1 / 100.0, 0.02), 0.999)
            xb = min(max(s2 / 100.0, 0.001), 0.98)
        if not xb < z < xd:
            self.result.setText(
                f"infeasible: need xb < z < xd  (xb {xb:.3f}, z {z:.3f}, "
                f"xd {xd:.3f})")
            return
        d_frac = (z - xb) / (xd - xb)
        D, B = d_frac * F, (1.0 - d_frac) * F
        S = (xd / (1.0 - xd)) / (xb / (1.0 - xb))
        eta = math.log(S) / (0.55 * n_st * math.log(alpha))
        if eta >= 1.0:
            self.result.setText(
                f"infeasible: needs tray efficiency {eta:.2f} > 1 - "
                "more stages, more alpha, or easier specs")
            return
        eta = max(eta, 0.02)
        R = -math.log(max(1.0 - eta, 1e-6)) / 0.42
        L = R * D
        t_bub = tl * z + th * (1.0 - z)
        q = min(max(1.0 + 2.4 * 720.0 * max(t_bub - tf, 0.0)
                    / 180000.0, 1.0), 1.25)
        V = L + D + (q - 1.0) * F
        steam = V / STEAM_M3_PER_TPH
        self.result.setText(
            f"D {D:.1f}   B {B:.1f}   reflux {L:.1f} m3/h   "
            f"R {R:.2f}   eta {eta:.2f}   boilup {V:.1f} m3/h   "
            f"steam {steam:.2f} t/h   log(S) {math.log10(S):.3f}   "
            f"q {q:.2f}")
        r_top = math.log(xd / (1.0 - xd))
        r_bot = math.log(xb / (1.0 - xb))
        self.plot.points = []
        for i in range(n_st + 1):
            f = i / n_st
            r = r_top + (r_bot - r_top) * f
            self.plot.points.append((f, 1.0 / (1.0 + math.exp(-r))))
        self.plot.update()
