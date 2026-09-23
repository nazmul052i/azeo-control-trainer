"""Column monitoring the reference way: temperature profile and
parallel coordinates, live.

The profile is synthesised from what the plant actually measures - the
two product analysers fix the ends, the stages distribute between them
by the same separation law the model integrates (log-linear in the
light/heavy ratio), pressure compensation rides on the measured
overhead pressure, and the three real tray transmitters are drawn as
dots ON the synthesised curve, so a mismatch between profile and
transmitters is information, not decoration.

The parallel-coordinates pane draws each column's operating point as a
polyline over its key variables, against a dashed reference captured
when the window opened - drift reads as the polyline peeling away from
its ghost.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..models.u500_u600_columns import ColumnT1, ColumnT2
from . import theme

T1_BLUE = theme.PROCESS_LINE
T2_GREEN = QColor("#1F8A3B")
MARK_RED = QColor("#C82020")

# (tag, lo, hi, short label) per axis of the parallel pane
AXES = [("FT-{n}001", 0, 200, "feed"),
        ("FT-{n}002", 0, 250, "reflux"),
        ("FT-{n}005", 0, 25, "steam"),
        ("FT-{n}003", 0, 120, "dist"),
        ("FT-{n}004", 0, 150, "btms"),
        ("PT-{n}001", 0, 15, "press"),
        ("AT-{n}001", 0, 10, "AT top"),
        ("AT-{n}002", 0, 10, "AT btm")]


def _profile(db, cls, n):
    """(stage_fraction, temperature) points plus the measured tray dots."""
    try:
        xd = 1.0 - float(db[f"AT-{n}001"].value) / 100.0
        xb = float(db[f"AT-{n}002"].value) / 100.0
        p = float(db[f"PT-{n}001"].value)
    except Exception:
        return [], []
    xd = min(max(xd, 0.02), 0.999)
    xb = min(max(xb, 0.001), 0.98)
    p_corr = (p - cls.P_NOMINAL) * 7.5
    import math
    r_top = math.log(xd / (1.0 - xd))
    r_bot = math.log(xb / (1.0 - xb))
    pts = []
    stages = int(cls.STAGES)
    for i in range(stages + 1):
        f = i / stages
        r = r_top + (r_bot - r_top) * f
        x = 1.0 / (1.0 + math.exp(-r))
        t = cls.T_LIGHT * x + cls.T_HEAVY * (1.0 - x) + p_corr
        pts.append((f, t))
    dots = []
    for frac, tag in ((0.25, f"TT-{n}002"), (0.50, f"TT-{n}003"),
                      (0.75, f"TT-{n}004")):
        try:
            dots.append((frac, float(db[tag].value)))
        except Exception:
            pass
    return pts, dots


class _ProfilePane(QWidget):
    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.setMinimumHeight(220)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)
        ml, mr, mt, mb = 46, 16, 26, 30
        t_lo, t_hi = 40.0, 300.0

        def xy(frac, t):
            x = ml + (t - t_lo) / (t_hi - t_lo) * (w - ml - mr)
            y = mt + frac * (h - mt - mb)
            return QPointF(min(max(x, ml), w - mr), y)

        p.setPen(QPen(theme.CANVAS_GRID, 1))
        for i in range(1, 6):
            x = ml + i / 6.0 * (w - ml - mr)
            p.drawLine(x, mt, x, h - mb)
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawLine(ml, mt, ml, h - mb)
        p.drawLine(ml, h - mb, w - mr, h - mb)
        p.setFont(QFont("Segoe UI", 8))
        for i in range(0, 7):
            t = t_lo + i / 6.0 * (t_hi - t_lo)
            p.drawText(ml + i / 6.0 * (w - ml - mr) - 12, h - 12,
                       f"{t:.0f}")
        p.drawText(6, mt - 8, "top")
        p.drawText(6, h - mb + 4, "btm")
        p.drawText(w - 130, mt - 8, "temperature profile  degC")

        for cls, n, col in ((ColumnT1, 5, T1_BLUE), (ColumnT2, 6, T2_GREEN)):
            pts, dots = _profile(self.db, cls, n)
            if not pts:
                continue
            poly = QPolygonF([xy(f, t) for f, t in pts])
            p.setPen(QPen(col, 2))
            p.drawPolyline(poly)
            p.drawText(xy(0.02, pts[0][1]).x() + 6, mt + 14
                       + (0 if n == 5 else 14), f"T{n - 4}")
            p.setPen(QPen(MARK_RED, 2))
            p.setBrush(MARK_RED)
            for f, t in dots:
                p.drawEllipse(xy(f, t), 4, 4)


class _ParallelPane(QWidget):
    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.setMinimumHeight(200)
        self._ref = {}          # first-seen reference values

    def _vals(self, n):
        out = []
        for tag, lo, hi, _lbl in AXES:
            try:
                v = float(self.db[tag.format(n=n)].value)
            except Exception:
                v = lo
            out.append((v - lo) / max(hi - lo, 1e-9))
        return out

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), theme.BACKGROUND)
        ml, mr, mt, mb = 30, 16, 30, 26
        na = len(AXES)

        def xy(i, f):
            x = ml + i / (na - 1) * (w - ml - mr)
            y = h - mb - min(max(f, 0.0), 1.0) * (h - mt - mb)
            return QPointF(x, y)

        p.setPen(QPen(theme.CANVAS_GRID, 1))
        p.setFont(QFont("Segoe UI", 7))
        for i, (_t, _lo, _hi, lbl) in enumerate(AXES):
            x = ml + i / (na - 1) * (w - ml - mr)
            p.drawLine(x, mt, x, h - mb)
            p.setPen(QPen(theme.OUTLINE, 1))
            p.drawText(x - 16, h - 10, lbl)
            p.setPen(QPen(theme.CANVAS_GRID, 1))
        p.setPen(QPen(theme.OUTLINE, 1))
        p.drawText(ml, mt - 10, "parallel coordinates - solid now, "
                                "dashed at window open")

        for n, col in ((5, T1_BLUE), (6, T2_GREEN)):
            vals = self._vals(n)
            if n not in self._ref:
                self._ref[n] = list(vals)
            p.setPen(QPen(col, 1, Qt.DashLine))
            p.drawPolyline(QPolygonF(
                [xy(i, f) for i, f in enumerate(self._ref[n])]))
            p.setPen(QPen(col, 2))
            p.drawPolyline(QPolygonF(
                [xy(i, f) for i, f in enumerate(vals)]))


class ColumnViews(QWidget):
    """Profile above, parallel coordinates below, one live window."""

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.db = db
        self.setWindowTitle("Column views  ·  AzeoPlant")
        self.setMinimumSize(560, 480)
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.profile = _ProfilePane(db)
        self.parallel = _ParallelPane(db)
        lay.addWidget(self.profile, 3)
        lay.addWidget(self.parallel, 2)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.profile.update)
        self._timer.timeout.connect(self.parallel.update)
        self._timer.start(1000)
