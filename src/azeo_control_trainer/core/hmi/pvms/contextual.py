"""Contextual display helpers used by the shipping station."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme.roles import Role
from ..theme.tokens import DEFAULT_THEME, THEMES


class TuningTrendWidget(QWidget):
    """Unsaved one-second PV/SP/OUT tuning trend popup."""

    POLL_MS = 1000

    def __init__(self, engine, paths: dict[str, str], *, theme=DEFAULT_THEME,
                 parent=None):
        super().__init__(parent)
        self.engine = engine
        self.palette = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.bindings = {name: engine.bind(path)
                         for name, path in paths.items() if path}
        self.history = {name: deque(maxlen=300) for name in self.bindings}
        self.setWindowTitle("Tuning Trend — 1 second PV / SP / OUT")
        self.setMinimumSize(520, 280)
        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.sample)
        self.timer.start()
        self.sample()

    def sample(self) -> None:
        self.engine.poll()
        for name, binding in self.bindings.items():
            value = binding.result.value
            try:
                self.history[name].append(float(value))
            except (TypeError, ValueError):
                self.history[name].append(None)
        self.update()

    def apply_operator_theme(self, theme):
        self.palette = THEMES[theme]
        self.update()

    def paintEvent(self, _event) -> None:          # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.palette[Role.SURFACE_PANEL]))
        chart = QRectF(self.rect()).adjusted(42, 20, -16, -32)
        painter.setPen(QPen(QColor(self.palette[Role.LINE_SOFT]), 1))
        painter.drawRect(chart)
        values = [value for rows in self.history.values() for value in rows
                  if value is not None]
        low, high = (min(values), max(values)) if values else (0.0, 1.0)
        if high <= low:
            high = low + 1.0
        colours = {"PV": Role.BAR_PV, "SP": Role.ACTION,
                   "OUT": Role.BAR_OUT}
        for name, rows in self.history.items():
            points = []
            data = list(rows)
            for index, value in enumerate(data):
                if value is None:
                    continue
                x = chart.right() - (len(data) - 1 - index) * max(
                    1.0, chart.width() / max(1, rows.maxlen - 1))
                y = chart.bottom() - (value - low) / (high - low) * chart.height()
                points.append((x, y))
            painter.setPen(QPen(QColor(self.palette[colours.get(
                name, Role.TEXT)]), 1.6))
            for first, second in zip(points, points[1:]):
                painter.drawLine(QPointF(*first), QPointF(*second))
        painter.setPen(QColor(self.palette[Role.TEXT_DIM]))
        painter.drawText(QRectF(4, chart.top(), 36, 18),
                         Qt.AlignRight, f"{high:.3g}")
        painter.drawText(QRectF(4, chart.bottom() - 18, 36, 18),
                         Qt.AlignRight, f"{low:.3g}")
        painter.end()

    def closeEvent(self, event) -> None:           # noqa: N802
        self.timer.stop()
        for binding in self.bindings.values():
            self.engine.unbind(binding)
        super().closeEvent(event)


def tuning_paths(pvm_cls, params: dict) -> dict[str, str]:
    """Resolve the conventional PV/SP/OUT declarations of a class."""
    from ..binding.engine import format_template
    keys = {"pv.value": "PV", "sp.value": "SP", "out.value": "OUT"}
    output = {}
    for spec in getattr(pvm_cls, "bindings", ()):
        label = keys.get(spec.key)
        if label and spec.path and not spec.prop and not spec.expr:
            try:
                output[label] = format_template(spec.path, params)
            except KeyError:
                pass
    return output


__all__ = ["TuningTrendWidget", "tuning_paths"]
