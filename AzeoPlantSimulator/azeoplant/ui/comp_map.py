"""The compressor map: head against suction flow, live.

The reference plant's signature window. Speed curves, the surge line,
the stonewall limit and the operating point are all drawn from the SAME
constants and formulas the U200 model integrates, so what the map shows
is what the machine will actually do - move the gas MW or the guide
vanes and the lines move with them, which is the entire anti-surge
formulation lesson.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..models.u200_compressor import RecycleCompressor as C1
from . import theme

MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 64, 20, 46, 44
Q_MAX = 100.0          # kNm3/h axis
H_MAX = 90.0           # kJ/kg axis
SPEED_FRACS = (0.55, 0.70, 0.80, 0.90, 1.00)

SURGE_GREEN = QColor("#1F8A3B")
CURVE_BLUE = theme.PROCESS_LINE
POINT_RED = QColor("#C82020")


class CompressorMap(QWidget):
    """Non-modal live map window, refreshed on a one-second timer."""

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.db = db
        self.setWindowTitle("Compressor map  ·  AzeoPlant")
        self.setMinimumSize(560, 420)
        self.resize(720, 520)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000)

    # ------------------------------------------------------------- helpers
    def _val(self, tag: str, default: float = 0.0) -> float:
        try:
            return float(self.db[tag].value)
        except Exception:
            return default

    def _xy(self, q: float, h: float, w: int, hgt: int):
        x = MARGIN_L + q / Q_MAX * (w - MARGIN_L - MARGIN_R)
        y = hgt - MARGIN_B - h / H_MAX * (hgt - MARGIN_T - MARGIN_B)
        return QPointF(x, y)

    # -------------------------------------------------------------- paint
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, hgt = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)

        mw = max(self._val("AT-2001", 12.0), 1.0)
        mwf = math.sqrt(max(min(14.0 / mw, 3.0), 0.3))
        gv_deg = self._val("GT-2001", 0.0)
        gv_frac = 1.0 - 0.55 * gv_deg / 40.0

        def head(q: float, sf: float) -> float:
            return max(C1.HEAD_COEFF * sf * sf * gv_frac * mwf
                       - C1.FLOW_COEFF * q * q, 0.0)

        def q_surge(sf: float) -> float:
            return (C1.SURGE_SLOPE * 40.0 * sf / math.sqrt(
                max(min(mw / 14.0, 3.0), 0.3)) * (0.70 + 0.30 * gv_frac))

        # grid + axes
        p.setPen(QPen(theme.CANVAS_GRID, 1))
        for i in range(1, 10):
            q = Q_MAX * i / 10.0
            p.drawLine(self._xy(q, 0, w, hgt), self._xy(q, H_MAX, w, hgt))
        for i in range(1, 9):
            h = H_MAX * i / 9.0
            p.drawLine(self._xy(0, h, w, hgt), self._xy(Q_MAX, h, w, hgt))
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawLine(self._xy(0, 0, w, hgt), self._xy(Q_MAX, 0, w, hgt))
        p.drawLine(self._xy(0, 0, w, hgt), self._xy(0, H_MAX, w, hgt))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(0, 11, 2):
            q = Q_MAX * i / 10.0
            pt = self._xy(q, 0, w, hgt)
            p.drawText(pt.x() - 10, pt.y() + 16, f"{q:.0f}")
        for i in range(0, 10, 3):
            h = H_MAX * i / 9.0
            pt = self._xy(0, h, w, hgt)
            p.drawText(6, pt.y() + 4, f"{h:.0f}")
        p.drawText(w // 2 - 60, hgt - 8, "suction flow  kNm3/h")
        p.save()
        p.translate(16, hgt // 2 + 60)
        p.rotate(-90)
        p.drawText(0, 0, "polytropic head  kJ/kg")
        p.restore()

        # speed curves, each from its surge flow out to stonewall
        p.setPen(QPen(CURVE_BLUE, 2))
        for sf in SPEED_FRACS:
            qs, qmax = q_surge(sf), min(C1.CHOKE_FLOW * sf, Q_MAX)
            pts = QPolygonF()
            steps = 40
            for i in range(steps + 1):
                q = qs + (qmax - qs) * i / steps
                pts.append(self._xy(q, head(q, sf), w, hgt))
            p.drawPolyline(pts)
            lbl = self._xy(qs, head(qs, sf), w, hgt)
            p.drawText(lbl.x() - 44, lbl.y() - 2,
                       f"{sf * C1.RATED_SPEED:.0f}")

        # surge line across the speed range
        p.setPen(QPen(SURGE_GREEN, 2))
        pts = QPolygonF()
        for i in range(31):
            sf = 0.4 + 0.6 * i / 30.0
            pts.append(self._xy(q_surge(sf), head(q_surge(sf), sf), w, hgt))
        p.drawPolyline(pts)
        p.drawText(self._xy(q_surge(0.62), head(q_surge(0.62), 0.62),
                            w, hgt).x() - 52,
                   self._xy(0, head(q_surge(0.62), 0.62), w, hgt).y(),
                   "SURGE")

        # stonewall
        p.setPen(QPen(theme.UTILITY_LINE, 1, Qt.DashLine))
        pts = QPolygonF()
        for i in range(31):
            sf = 0.4 + 0.6 * i / 30.0
            q = min(C1.CHOKE_FLOW * sf, Q_MAX)
            pts.append(self._xy(q, head(q, sf), w, hgt))
        p.drawPolyline(pts)
        p.drawText(self._xy(76.0, 6.0, w, hgt), "STONEWALL")

        # operating point
        q_op = self._val("FT-2001") + self._val("FT-2002")
        ps = self._val("PT-2001", 9.0) + 1.013
        pd = self._val("PT-2002", 18.0) + 1.013
        h_op = max(pd / max(ps, 0.05) - 1.0, 0.0) * 55.0
        pt = self._xy(min(q_op, Q_MAX), min(h_op, H_MAX), w, hgt)
        p.setPen(QPen(POINT_RED, 2))
        p.setBrush(POINT_RED)
        p.drawEllipse(pt, 5, 5)

        # readout line
        p.setPen(QPen(theme.OUTLINE, 1))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(
            MARGIN_L, 24,
            f"speed {self._val('ST-2001'):.0f} rpm    "
            f"margin {self._val('UY-2001'):.0f} %    "
            f"power {self._val('JT-2001'):.2f} MW    "
            f"MW {mw:.1f} kg/kmol    vanes {gv_deg:.0f} deg")
