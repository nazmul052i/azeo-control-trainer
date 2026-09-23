"""Measured Azeo-style surfaces for trainer-specific faceplates.

These trainer-specific contracts use the shared Azeo Operator Station
function-block vocabulary: fixed logical
coordinates, the 224/226/235 neutral surface, dark-blue process values,
teal operator values, compact commands and one faceplate-return action.
They are explicit adaptations for trainer blocks.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

from ..theme.roles import Role
from .faceplate_icons import draw_faceplate_icon
from .loop_surface import LoopFaceplateSurface, _value


class _TrainerSurface(LoopFaceplateSurface):
    """Fixed-coordinate faceplate base with honest, serviceable hotspots."""

    WHOLE_SURFACE = True
    ACTIONS = ("faceplate",)
    DESIGN_WIDTH = 250.0
    DESIGN_HEIGHT = 300.0
    FULL_HEIGHT = 300
    MINI_HEIGHT = 300

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.setFixedSize(round(self.DESIGN_WIDTH), self.FULL_HEIGHT)
        self._commands: dict[str, tuple[QRectF, str, object]] = {}
        self._pressed_command: str | None = None
        self._trend_values: deque[float] = deque(maxlen=48)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(round(self.DESIGN_WIDTH), self.FULL_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_identity(self, path: str, description: str = "") -> None:
        first = str(path).split(" · ", 1)[0]
        module, separator, block = first.partition("/")
        self.set_labels(
            eyebrow=str(path) if separator else "",
            title=module or description,
            subtitle=description,
            unit_name=module or block,
        )

    def _begin(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        scale, ox, oy = self._transform()
        painter.fillRect(self.rect(), self.BG)
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        gradient = QLinearGradient(0, 0, self.DESIGN_WIDTH, 0)
        gradient.setColorAt(0.0, self.BG)
        gradient.setColorAt(0.55, self.BG_ALT)
        gradient.setColorAt(1.0, self.BG)
        painter.fillRect(
            QRectF(0, 0, self.DESIGN_WIDTH, self.DESIGN_HEIGHT),
            QBrush(gradient),
        )

    def _identity(self, painter: QPainter, *, title_size: int = 16) -> None:
        self._text(painter, QRectF(5, 4, self.DESIGN_WIDTH - 45, 15),
                   self.state.eyebrow, 8, self.TEXT)
        self._text(painter, QRectF(5, 19, self.DESIGN_WIDTH - 45, 27),
                   self.state.title, title_size, self.TEXT)
        self._text(painter, QRectF(5, 44, self.DESIGN_WIDTH - 10, 17),
                   self.state.subtitle, 9, self.TEXT)
        if "faceplate" in self._available_actions:
            draw_faceplate_icon(
                painter,
                QRectF(self.DESIGN_WIDTH - 36, 13, 31, 31),
                "faceplate_return",
                button=True,
                pressed=self._pressed == "return",
            )

    def _caption_value(self, painter: QPainter, y: float, caption: str,
                       value, *, colour: QColor | None = None,
                       suffix: str = "", value_x: float | None = None) -> None:
        value_x = value_x if value_x is not None else self.DESIGN_WIDTH * .54
        self._text(painter, QRectF(12, y, value_x - 16, 18), caption,
                   9, self.TEXT, left=True)
        text = self._display(value)
        if suffix and text != "#######":
            text += suffix
        self._text(painter, QRectF(value_x, y, self.DESIGN_WIDTH-value_x-9, 18),
                   text, 10, colour or self.VALUE, bold=True, left=True)

    def _horizontal_bar(self, painter: QPainter, rect: QRectF, value,
                        minimum: float, maximum: float,
                        *, colour: QColor | None = None,
                        marker=None) -> None:
        painter.setPen(QPen(self.GRID, .8))
        painter.setBrush(self._furniture(Role.SURFACE_SUNK, 204, 205, 207))
        painter.drawRect(rect)
        if isinstance(value, (int, float)):
            fraction = self._norm(float(value), minimum, maximum)
            painter.fillRect(QRectF(rect.left(), rect.top(),
                                   rect.width() * fraction, rect.height()),
                             colour or self.TEAL)
        if isinstance(marker, (int, float)):
            fraction = self._norm(float(marker), minimum, maximum)
            x = rect.left() + rect.width() * fraction
            painter.setPen(QPen(self.TEXT, 1.2))
            painter.drawLine(QPointF(x, rect.top() - 4),
                             QPointF(x, rect.bottom() + 4))

    def _trend_box(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, self.PLOT_BG)
        inner = rect.adjusted(24, 6, -4, -7)
        painter.setPen(QPen(self._furniture(Role.LINE, 132, 132, 132), .8))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(inner)
        values = list(self._trend_values)
        if len(values) < 2:
            return
        minimum, maximum = min(values), max(values)
        if abs(maximum - minimum) < 1e-9:
            maximum = minimum + 1.0
        path = QPainterPath()
        for index, value in enumerate(values):
            x = inner.left() + inner.width() * index / (len(values) - 1)
            y = inner.bottom() - self._norm(value, minimum, maximum) * inner.height()
            path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
        painter.setPen(QPen(self.BLUE, 1.15))
        painter.drawPath(path)

    def _command(self, painter: QPainter, rect: QRectF, key: str,
                 caption: str, value=True) -> None:
        self._commands[key] = (rect, key, value)
        enabled = self._write_allowed(key)
        painter.setPen(QPen(
            self._furniture(Role.LINE, 158, 160, 164) if enabled else self.MUTED, 1))
        painter.setBrush(
            self._furniture(Role.SURFACE_FIELD, 241, 241, 242) if enabled and self._pressed_command != key
            else self._furniture(Role.SELECTION, 211, 218, 225) if enabled
            else self.BG_ALT)
        painter.drawRect(rect)
        self._text(
            painter, rect, caption, 10,
            self.TEXT if enabled else self.MUTED, bold=True)

    def _trainer_hit(self, point: QPointF) -> str | None:
        action = QRectF(self.DESIGN_WIDTH - 38, 11, 36, 36)
        if "faceplate" in self._available_actions and action.contains(point):
            return "return"
        for name, (rect, key, _command_value) in self._commands.items():
            if self._write_allowed(key) and rect.contains(point):
                return name
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Trainer adaptations paint neither Loop_fp's slews nor its mode and
        # alarm controls.  Reusing Loop's hit map gave those invisible regions
        # a pointing cursor after the mouse crossed them.
        hit = self._trainer_hit(self._design_point(event.position()))
        if hit != self._hover:
            self._hover = hit
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if hit
                else Qt.CursorShape.ArrowCursor)
            self.update()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        hit = self._trainer_hit(self._design_point(event.position()))
        if hit == "return":
            self._pressed = "return"
            self.update()
            event.accept()
            return
        if hit is not None:
            self._pressed_command = hit
            self.update()
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        point = self._design_point(event.position())
        released = self._trainer_hit(point)
        if self._pressed == "return":
            self._pressed = None
            self.update()
            if released == "return":
                self.action_requested.emit("faceplate")
            event.accept()
            return
        name = self._pressed_command
        self._pressed_command = None
        self.update()
        if name is not None:
            _rect, key, value = self._commands[name]
            if released == name and self._write_allowed(key):
                self.write_requested.emit(key, value)
            event.accept()
            return
        event.ignore()

    @staticmethod
    def _display(value) -> str:
        if value is None:
            return "#######"
        if isinstance(value, bool):
            return "ACTIVE" if value else "NORMAL"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)


class CascadePairFaceplateSurface(_TrainerSurface):
    DESIGN_WIDTH = 280.0
    DESIGN_HEIGHT = 250.0
    FULL_HEIGHT = 250
    MINI_HEIGHT = 250

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.values = {}
        self.cascade_broken = False

    def refresh(self, bound) -> None:
        self.values = {key: _value(bound, key) for key in (
            "master.pv", "master.sp", "master.out", "master.mode",
            "slave.pv", "slave.sp", "slave.out", "slave.mode")}
        for key in ("master.pv", "slave.pv"):
            value = self.values.get(key)
            if isinstance(value, (int, float)):
                self._trend_values.append(float(value))
        slave_mode = str(self.values.get("slave.mode") or "").upper()
        self.cascade_broken = bool(slave_mode and slave_mode != "CAS")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._begin(painter)
        self._identity(painter, title_size=15)
        for top, prefix, caption in ((68, "master", "CASCADE MASTER"),
                                     (142, "slave", "CASCADE SLAVE")):
            painter.setPen(QPen(self.GRID, .8))
            painter.setBrush(self._furniture(Role.SURFACE_PANEL_ALT, 231, 232, 235))
            painter.drawRect(QRectF(10, top, 260, 64))
            self._text(painter, QRectF(17, top + 4, 105, 16), caption,
                       9, self.TEXT, bold=True, left=True)
            self._text(painter, QRectF(214, top + 4, 49, 16),
                       self._display(self.values.get(prefix + ".mode")),
                       9, self.VALUE, bold=True)
            for x, key, label in ((18, ".pv", "PV"), (98, ".sp", "SP"),
                                  (178, ".out", "OUT")):
                self._text(painter, QRectF(x, top + 24, 34, 14), label,
                           8, self.TEXT, left=True)
                self._text(painter, QRectF(x, top + 38, 70, 18),
                           self._display(self.values.get(prefix + key)),
                           11, self.VALUE if key != ".out" else self.TEAL,
                           bold=True, left=True)
        self._text(painter, QRectF(10, 213, 260, 19),
                   "CASCADE OPEN · SLAVE NOT IN CAS" if self.cascade_broken
                   else "CASCADE CONNECTED", 9,
                   QColor(170, 25, 25) if self.cascade_broken else self.TEXT,
                   bold=True)
        painter.end()


class PassBalanceFaceplateSurface(_TrainerSurface):
    DESIGN_WIDTH = 308.0
    DESIGN_HEIGHT = 300.0
    FULL_HEIGHT = 300
    MINI_HEIGHT = 300

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.values = {}

    def refresh(self, bound) -> None:
        self.values = {key: _value(bound, key) for key in (
            "a.value", "b.value", "c.value", "d.value", "mean", "spread")}
        mean = self.values.get("mean")
        if isinstance(mean, (int, float)):
            self._trend_values.append(float(mean))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._begin(painter)
        self._identity(painter)
        numeric = [float(value) for key, value in self.values.items()
                   if key.endswith(".value") and isinstance(value, (int, float))]
        minimum = min(numeric) if numeric else 0.0
        maximum = max(numeric) if numeric else 100.0
        if maximum <= minimum:
            maximum = minimum + 1.0
        for index, (key, caption) in enumerate((
                ("a.value", "PASS A"), ("b.value", "PASS B"),
                ("c.value", "PASS C"), ("d.value", "PASS D"))):
            y = 72 + index * 35
            self._text(painter, QRectF(14, y, 52, 17), caption, 9,
                       self.TEXT, bold=True, left=True)
            self._horizontal_bar(painter, QRectF(70, y + 2, 155, 10),
                                 self.values.get(key), minimum, maximum)
            self._text(painter, QRectF(232, y - 1, 68, 18),
                       self._display(self.values.get(key)), 10,
                       self.VALUE, bold=True, left=True)
        self._caption_value(painter, 218, "MEAN", self.values.get("mean"),
                            value_x=90)
        self._caption_value(painter, 239, "SPREAD", self.values.get("spread"),
                            colour=self.TEAL, value_x=90)
        self._trend_box(painter, QRectF(10, 263, 288, 30))
        painter.end()


class RatioFaceplateSurface(_TrainerSurface):
    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.values = {}

    def refresh(self, bound) -> None:
        self.values = {key: _value(bound, key) for key in (
            "field.value", "out.value", "ratio.actual", "ratio.target",
            "ratio.bias", "ratio.hi", "ratio.lo")}
        actual = self.values.get("ratio.actual")
        if isinstance(actual, (int, float)):
            self._trend_values.append(float(actual))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._begin(painter)
        self._identity(painter)
        self._caption_value(painter, 72, "INPUT", self.values.get("field.value"))
        self._caption_value(painter, 94, "OUTPUT", self.values.get("out.value"),
                            colour=self.TEAL)
        self._caption_value(painter, 125, "ACTUAL RATIO",
                            self.values.get("ratio.actual"))
        self._caption_value(painter, 147, "TARGET RATIO",
                            self.values.get("ratio.target"), colour=self.TEAL)
        self._caption_value(painter, 169, "BIAS", self.values.get("ratio.bias"))
        low = self.values.get("ratio.lo")
        high = self.values.get("ratio.hi")
        minimum = float(low) if isinstance(low, (int, float)) else 0.0
        maximum = float(high) if isinstance(high, (int, float)) else 2.0
        self._horizontal_bar(painter, QRectF(14, 198, 222, 13),
                             self.values.get("ratio.actual"), minimum, maximum,
                             marker=self.values.get("ratio.target"))
        self._trend_box(painter, QRectF(10, 222, 230, 69))
        painter.end()


class TotalizerFaceplateSurface(_TrainerSurface):
    DESIGN_HEIGHT = 330.0
    FULL_HEIGHT = 330
    MINI_HEIGHT = 330

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.values = {}

    def refresh(self, bound) -> None:
        self.values = {key: _value(bound, key) for key in (
            "total", "rate", "target", "enable")}
        total = self.values.get("total")
        if isinstance(total, (int, float)):
            self._trend_values.append(float(total))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        self._commands.clear()
        painter = QPainter(self)
        self._begin(painter)
        self._identity(painter)
        self._text(painter, QRectF(12, 70, 226, 16), "ACCUMULATED TOTAL",
                   9, self.TEXT, bold=True, left=True)
        self._text(painter, QRectF(12, 87, 226, 30),
                   self._display(self.values.get("total")), 18,
                   self.VALUE, bold=True, left=True)
        self._caption_value(painter, 124, "RATE", self.values.get("rate"))
        self._caption_value(painter, 146, "TARGET", self.values.get("target"),
                            colour=self.TEAL)
        enabled = bool(self.values.get("enable"))
        self._text(painter, QRectF(12, 172, 226, 20),
                   "TOTALIZING" if enabled else "TOTALIZER HELD", 10,
                   self.TEAL if enabled else self.TEXT, bold=True)
        target = self.values.get("target")
        maximum = float(target) if isinstance(target, (int, float)) and target else 100.0
        self._horizontal_bar(painter, QRectF(14, 198, 222, 13),
                             self.values.get("total"), 0.0, maximum)
        self._trend_box(painter, QRectF(10, 220, 230, 66))
        self._command(painter, QRectF(69, 295, 112, 25),
                      "cmd.reset", "RESET TOTAL")
        painter.end()


class RampSoakFaceplateSurface(_TrainerSurface):
    """ERAMP figure anatomy adapted to the trainer's start/stop/reset pins."""

    DESIGN_WIDTH = 244.0
    DESIGN_HEIGHT = 385.0
    FULL_HEIGHT = 385
    MINI_HEIGHT = 385

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(palette, parent)
        self.values = {}

    def refresh(self, bound) -> None:
        self.values = {key: _value(bound, key) for key in (
            "pv.value", "field.value", "state.running", "state.complete")}
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        self._commands.clear()
        painter = QPainter(self)
        self._begin(painter)
        self._identity(painter)
        running = bool(self.values.get("state.running"))
        complete = bool(self.values.get("state.complete"))
        self._caption_value(painter, 70, "Ramp Status",
                            "COMPLETE" if complete else
                            "RUNNING" if running else "STOPPED",
                            colour=self.TEAL, value_x=135)
        self._caption_value(painter, 94, "Output",
                            self.values.get("pv.value"), value_x=135)
        self._caption_value(painter, 118, "Segment",
                            self.values.get("field.value"), value_x=135)
        self._command(painter, QRectF(71, 161, 102, 28),
                      "cmd.start", "Start Ramp")
        self._command(painter, QRectF(71, 202, 102, 28),
                      "cmd.stop", "Stop Ramp")
        self._command(painter, QRectF(71, 243, 102, 28),
                      "cmd.reset", "Reset Ramp")
        self._caption_value(painter, 292, "Ramp End Value",
                            self.values.get("pv.value"), value_x=150)
        self._caption_value(painter, 320, "Ramp Unit", "DATA", value_x=150)
        self._caption_value(painter, 348, "Ramp Time", "#######",
                            value_x=150)
        painter.end()


WHOLE_SURFACES = {
    "CascadePairFaceplate": CascadePairFaceplateSurface,
    "PassBalanceFaceplate": PassBalanceFaceplateSurface,
    "RatioFaceplate": RatioFaceplateSurface,
    "TotalizerFaceplate": TotalizerFaceplateSurface,
    "RampSoakFaceplate": RampSoakFaceplateSurface,
}


__all__ = [
    "CascadePairFaceplateSurface",
    "PassBalanceFaceplateSurface",
    "RampSoakFaceplateSurface",
    "RatioFaceplateSurface",
    "TotalizerFaceplateSurface",
    "WHOLE_SURFACES",
]
