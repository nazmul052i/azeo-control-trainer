"""The pH response window: the titration curve, live.

The reference's signature effluent display - pH against reagent with
the operating point marked, and the local process gain read off the
curve. Drawn from the SAME curve the U800 model integrates, so what it
shows is what the loop is actually fighting: thousands of units of
gain at neutrality, almost none out on the flat ends.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..models.u800_u900 import EffluentTreatment
from . import theme

MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 56, 20, 40, 40
X_MAX = 0.10           # excess-reagent axis, +-, in the model's units
CURVE_BLUE = theme.PROCESS_LINE
POINT_RED = QColor("#C82020")
SP_GREEN = QColor("#1F8A3B")


class PhCurve(QWidget):
    """Non-modal live titration-curve window."""

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.db = db
        self.setWindowTitle("pH response  ·  AzeoPlant")
        self.setMinimumSize(480, 380)
        self.resize(640, 460)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000)

    def _xy(self, x: float, ph: float, w: int, hgt: int) -> QPointF:
        fx = (x + X_MAX) / (2.0 * X_MAX)
        px = MARGIN_L + fx * (w - MARGIN_L - MARGIN_R)
        py = hgt - MARGIN_B - ph / 14.0 * (hgt - MARGIN_T - MARGIN_B)
        return QPointF(px, py)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, hgt = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)

        curve = EffluentTreatment._ph_from_excess

        # grid and axes
        p.setPen(QPen(theme.CANVAS_GRID, 1))
        for i in range(1, 14):
            y = self._xy(0, float(i), w, hgt).y()
            p.drawLine(MARGIN_L, y, w - MARGIN_R, y)
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawLine(self._xy(-X_MAX, 0, w, hgt), self._xy(X_MAX, 0, w, hgt))
        p.drawLine(self._xy(-X_MAX, 0, w, hgt), self._xy(-X_MAX, 14, w, hgt))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(0, 15, 2):
            pt = self._xy(-X_MAX, float(i), w, hgt)
            p.drawText(8, pt.y() + 4, f"{i}")
        p.drawText(w // 2 - 90, hgt - 8,
                   "excess acid  <-   reagent   ->  excess caustic")
        p.save()
        p.translate(20, hgt // 2 + 10)
        p.rotate(-90)
        p.drawText(0, 0, "pH")
        p.restore()

        # the curve (x is excess ACID: positive = acidic = low pH)
        p.setPen(QPen(CURVE_BLUE, 2))
        pts = QPolygonF()
        n = 240
        for i in range(n + 1):
            x = -X_MAX + 2.0 * X_MAX * i / n
            pts.append(self._xy(-x, curve(x), w, hgt))
        p.drawPolyline(pts)

        # setpoint line and operating point
        try:
            sp = float(7.0)
            pv = float(self.db["AT-8002"].value)
        except Exception:
            sp, pv = 7.0, 7.0
        p.setPen(QPen(SP_GREEN, 1, Qt.DashLine))
        y_sp = self._xy(0, sp, w, hgt).y()
        p.drawLine(MARGIN_L, y_sp, w - MARGIN_R, y_sp)

        # invert the curve for the operating point's x
        x_op = 0.004 * ((10.0 ** (abs(7.0 - pv) / 3.2) - 1.0)
                        * (1.0 if pv < 7.0 else -1.0))
        pt = self._xy(-x_op, pv, w, hgt)
        p.setPen(QPen(POINT_RED, 2))
        p.setBrush(POINT_RED)
        p.drawEllipse(pt, 5, 5)

        # local slope = the process gain the PID actually sees
        eps = 1e-4
        kp = abs(curve(x_op + eps) - curve(x_op - eps)) / (2 * eps)
        p.setPen(QPen(theme.OUTLINE, 1))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(MARGIN_L, 22,
                   f"pH {pv:.2f}    local gain {kp:,.0f} pH per unit reagent"
                   f"    (at neutrality {abs(curve(eps) - curve(-eps)) / (2 * eps):,.0f})")
