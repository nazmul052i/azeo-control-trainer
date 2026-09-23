"""Predicting heating value - the reference's QY11 teaching plot.

Heating value against specific gravity: the paraffins lie on a
straight line, which is what makes SG a usable one-analyser inference
- and hydrogen, CO, CO2, N2 and H2S lie far off it, which is what
makes the inference honestly wrong when the header runs offgas-rich.
The red point is the LIVE header gas (AT-0102 SG, AT-0101 measured
HV); its distance from the dashed line is the error the total-duty
oil trim is quietly carrying.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme

# (name, SG, MJ/sm3)
COMPONENTS = [("H2", 0.0695, 10.8), ("CH4", 0.554, 35.8),
              ("C2H6", 1.038, 63.7), ("C3H8", 1.522, 93.9),
              ("N2", 0.967, 0.0), ("CO2", 1.519, 0.0),
              ("CO", 0.967, 11.6), ("H2S", 1.188, 23.1)]
PARAFFINS = ("H2", "CH4", "C2H6", "C3H8")
SG_MAX, HV_MAX = 1.7, 100.0
POINT_BLUE = theme.PROCESS_LINE
LIVE_RED = QColor("#C82020")


class FuelQuality(QWidget):
    def __init__(self, db, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.db = db
        self.setWindowTitle("Fuel quality  ·  AzeoPlant")
        self.setMinimumSize(480, 380)
        self.resize(620, 460)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000)

    def _xy(self, sg: float, hv: float, w: int, h: int) -> QPointF:
        ml, mr, mt, mb = 52, 16, 34, 36
        x = ml + sg / SG_MAX * (w - ml - mr)
        y = h - mb - hv / HV_MAX * (h - mt - mb)
        return QPointF(x, y)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)

        p.setPen(QPen(theme.CANVAS_GRID, 1))
        for i in range(1, 9):
            pt = self._xy(SG_MAX * i / 8.0, 0, w, h)
            p.drawLine(pt, self._xy(SG_MAX * i / 8.0, HV_MAX, w, h))
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawLine(self._xy(0, 0, w, h), self._xy(SG_MAX, 0, w, h))
        p.drawLine(self._xy(0, 0, w, h), self._xy(0, HV_MAX, w, h))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(0, 9, 2):
            pt = self._xy(SG_MAX * i / 8.0, 0, w, h)
            p.drawText(pt.x() - 10, pt.y() + 16, f"{SG_MAX * i / 8.0:.1f}")
        for i in range(0, 6):
            pt = self._xy(0, HV_MAX * i / 5.0, w, h)
            p.drawText(10, pt.y() + 4, f"{HV_MAX * i / 5.0:.0f}")
        p.drawText(w // 2 - 70, h - 8, "specific gravity  (air = 1)")
        p.save()
        p.translate(16, h // 2 + 40)
        p.rotate(-90)
        p.drawText(0, 0, "heating value  MJ/sm3")
        p.restore()

        # the paraffin regression the inference uses: HV = 30 + (SG-0.55)/0.28*15
        p.setPen(QPen(LIVE_RED, 1, Qt.DashLine))
        p.drawLine(self._xy(0.0, 30.0 - 0.55 / 0.28 * 15.0, w, h),
                   self._xy(1.35, 30.0 + 0.80 / 0.28 * 15.0, w, h))

        p.setPen(QPen(POINT_BLUE, 2))
        p.setBrush(POINT_BLUE)
        for name, sg, hv in COMPONENTS:
            pt = self._xy(sg, hv, w, h)
            p.drawEllipse(pt, 4, 4)
            p.drawText(pt.x() + 7, pt.y() + 4, name)

        try:
            sg = float(self.db["AT-0102"].value)
            hv = float(self.db["AT-0101"].value)
        except Exception:
            return
        hv_inf = max(20.0, min(50.0, 30.0 + (sg - 0.55) / 0.28 * 15.0))
        pt = self._xy(min(sg, SG_MAX), min(hv, HV_MAX), w, h)
        p.setPen(QPen(LIVE_RED, 2))
        p.setBrush(LIVE_RED)
        p.drawEllipse(pt, 5, 5)
        p.setPen(QPen(theme.OUTLINE, 1))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(56, 22,
                   f"header gas: SG {sg:.3f}   measured HV {hv:.1f}   "
                   f"inferred {hv_inf:.1f}   error {hv - hv_inf:+.1f} MJ/sm3")
