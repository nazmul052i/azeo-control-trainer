"""Measured Azeo ``DC_fp`` device-control faceplate.

The extracted Live help figure is a fixed 250 x 560 operator surface.  It is
not a form: the status marks, state commands, mode pair, transition timing,
device/failure state and trend all have stable locations an operator learns.
This painter therefore owns one measured coordinate system, in the same way
as :mod:`loop_surface` and :mod:`analog_surface`.

Presentation deliberately stops at the binding boundary.  In particular the
current ``DeviceFaceplate`` contract has no BYPASSED, PERMISSIVE, SIMULATE,
ACCEPT or DELAY_TIMER binding.  Their documented positions remain available,
but ``refresh`` leaves those indications absent instead of painting a healthy
state the controller never supplied.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from ..theme.fonts import FontRole, font_for
from ..theme.roles import Role
from .faceplate_icons import draw_faceplate_icon


def _result(bound, key):
    binding = bound.get(key)
    return None if binding is None or isinstance(binding, tuple) \
        else binding.result


class DeviceFaceplateSurface(QWidget):
    """High-fidelity ``DC_fp`` surface driven by ``DeviceFaceplate`` keys."""

    WHOLE_SURFACE = True
    DESIGN_WIDTH = 250.0
    DESIGN_HEIGHT = 560.0
    ACTIONS = ("detail", "faceplate")

    #: State/failure names come from ``DevctlBlock``'s published integer
    #: contract.  Unknown codes remain explicit; they are never coerced to a
    #: reassuring word such as NORMAL.
    STATE_NAMES = {
        0: "Confirmed Stopped",
        1: "Starting",
        2: "Confirmed Running",
        3: "Stopping",
        4: "Faulted",
        5: "Shutdown / Interlocked",
        6: "Locked",
    }
    FAIL_NAMES = {
        0: "Clear",
        1: "Passive confirm timeout",
        2: "Active confirm timeout",
        5: "Active confirm lost",
        7: "Tripped",
        8: "Shutdown",
    }

    write_requested = Signal(str, object)
    action_requested = Signal(str)

    _STATUS_RECTS = {
        "interlocked": QRectF(14, 45, 20, 20),
        "bypassed": QRectF(14, 73, 20, 20),
        "no_permit": QRectF(14, 101, 20, 20),
        "simulate": QRectF(216, 45, 20, 20),
    }
    _COMMAND_RECTS = {
        "cmd.start": QRectF(88, 87, 76, 22),
        "cmd.stop": QRectF(88, 116, 76, 22),
    }
    _RESET_RECT = QRectF(174, 306, 55, 27)
    _TREND_RECT = QRectF(25, 398, 200, 114)
    _ACTION_RECTS = {
        "detail": QRectF(66, 524, 32, 32),
        "faceplate": QRectF(152, 524, 32, 32),
    }

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.eyebrow = ""
        self.title = ""
        self.subtitle = "Device Control"

        self.pv_text = "#######"
        self.pv_bad = True
        self.state_code: int | None = None
        self.state_name = "\u2014"
        self.running: bool | None = None
        self.locked: bool | None = None
        self.paused: bool | None = None
        self.fail_active: bool | None = None
        self.fail_code: int | None = None
        self.failure_text = "\u2014"

        self.target_mode = ""
        self.actual_mode = ""
        self.normal_mode = ""
        self.target_mode_mismatch = False
        self.actual_mode_mismatch = False

        self.transition_limit: float | None = None
        self.elapsed: float | None = None
        self.delay_remaining: float | None = None

        # ``None`` is important: it means the source cannot answer.  Painting
        # False here would say the condition was checked and is healthy.
        self.interlocked: bool | None = None
        self.bypassed: bool | None = None
        self.no_permit: bool | None = None
        self.simulate_active: bool | None = None
        self.accepted: bool | None = None

        self._trend: deque[float] = deque(maxlen=72)
        self._available_actions: set[str] = set()
        self._write_permissions: frozenset[str] | None = None
        self._hover: str | None = None
        self._pressed: str | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(round(self.DESIGN_WIDTH), round(self.DESIGN_HEIGHT))
        self.apply_theme(palette)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(round(self.DESIGN_WIDTH), round(self.DESIGN_HEIGHT))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    # ------------------------------------------------------------ public API
    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self._colors = {role: QColor(value) for role, value in palette.items()}
        self.update()

    def set_identity(self, path: str, description: str = "Device Control") -> None:
        module, separator, block = str(path).partition("/")
        self.eyebrow = str(path) if separator else ""
        self.title = module or block or description
        self.subtitle = description
        self.update()

    def set_available_actions(self, keys) -> None:
        self._available_actions = {
            str(key) for key in (keys or ())
        }.intersection(self.ACTIONS)
        self.update()

    def set_write_permissions(self, keys) -> None:
        self._write_permissions = frozenset(str(key) for key in (keys or ()))
        self.update()

    @property
    def writable_keys(self) -> frozenset[str]:
        return self._write_permissions or frozenset()

    def _write_allowed(self, key: str) -> bool:
        return self._write_permissions is None or key in self._write_permissions

    @property
    def available_actions(self) -> frozenset[str]:
        return frozenset(self._available_actions)

    @classmethod
    def status_icon_geometry(cls, name: str) -> QRectF:
        return QRectF(cls._STATUS_RECTS[name])

    @classmethod
    def state_button_geometry(cls, key: str) -> QRectF:
        return QRectF(cls._COMMAND_RECTS[key])

    @classmethod
    def reset_geometry(cls) -> QRectF:
        return QRectF(cls._RESET_RECT)

    @classmethod
    def trend_geometry(cls) -> QRectF:
        return QRectF(cls._TREND_RECT)

    @classmethod
    def action_geometry(cls, key: str) -> QRectF:
        return QRectF(cls._ACTION_RECTS[key])

    @property
    def reset_visible(self) -> bool:
        # DC_fp's Reset is specifically the DC_STATE Locked reset.  Showing
        # it for every failure would contradict the vendor surface even though
        # the underlying trainer command can clear a fault.
        return self.locked is True or self.state_code == 6

    def replace_trend(self, values: Iterable[float]) -> None:
        self._trend.clear()
        self._trend.extend(float(value) for value in values)
        self.update()

    def refresh(self, bound) -> None:
        """Translate one binding snapshot without manufacturing state.

        Only keys declared today by :class:`DeviceFaceplate` are read here.
        The documented but currently unmodelled Bypass, Permit, Simulation,
        Accept and delay states remain ``None`` and therefore absent.
        """
        self.pv_text = "#######"
        self.pv_bad = True
        self.state_code = None
        self.state_name = "\u2014"
        self.running = None
        self.locked = None
        self.paused = None
        self.fail_active = None
        self.fail_code = None
        self.failure_text = "\u2014"
        self.target_mode = ""
        self.actual_mode = ""
        self.normal_mode = ""
        self.target_mode_mismatch = False
        self.actual_mode_mismatch = False
        self.transition_limit = None
        self.elapsed = None
        self.delay_remaining = None
        self.interlocked = None
        self.bypassed = None
        self.no_permit = None
        self.simulate_active = None
        self.accepted = None

        state = _result(bound, "state.value") or _result(bound, "pv.value")
        if state is not None and isinstance(state.value, (int, float)):
            self.state_code = int(state.value)
            self.state_name = self.STATE_NAMES.get(
                self.state_code, f"State {self.state_code}")
            if state.quality.name == "GOOD":
                self._trend.append(float(self.state_code))
                self.interlocked = self.state_code == 5

        field = _result(bound, "field.value")
        if field is None:
            field = _result(bound, "state.running")
        if field is not None:
            self.pv_bad = field.quality.name == "BAD"
            if not self.pv_bad and isinstance(field.value, bool):
                self.running = field.value
                self.pv_text = "RUNNING" if field.value else "STOPPED"
            elif not self.pv_bad and field.value is not None:
                self.pv_text = str(field.value)

        locked = _result(bound, "state.locked")
        if locked is not None and locked.quality.name != "BAD" \
                and isinstance(locked.value, bool):
            self.locked = locked.value

        paused = _result(bound, "state.paused")
        if paused is not None and paused.quality.name != "BAD" \
                and isinstance(paused.value, bool):
            self.paused = paused.value

        failed = _result(bound, "state.fail")
        if failed is not None and failed.quality.name != "BAD" \
                and isinstance(failed.value, bool):
            self.fail_active = failed.value

        fail_code = _result(bound, "state.fail_code")
        if fail_code is not None and isinstance(fail_code.value, (int, float)):
            self.fail_code = int(fail_code.value)
            self.failure_text = self.FAIL_NAMES.get(
                self.fail_code, f"Failure {self.fail_code}")
        elif self.fail_active is False:
            # This is not a default: FAIL_ACTIVE=False is an explicit read.
            self.failure_text = "Clear"

        elapsed = _result(bound, "elapsed")
        if elapsed is not None and isinstance(elapsed.value, (int, float)):
            self.elapsed = float(elapsed.value)

        limit_key = {
            1: "time.start_limit",
            3: "time.stop_limit",
        }.get(self.state_code)
        limit = _result(bound, limit_key) if limit_key else None
        if limit is not None and isinstance(limit.value, (int, float)):
            self.transition_limit = float(limit.value)

        mode = _result(bound, "mode")
        if mode is not None and mode.quality.name != "BAD":
            self.actual_mode = str(mode.mode_actual or mode.value or "").upper()
            self.target_mode = str(mode.mode_target or "").upper()
            self.normal_mode = str(mode.mode_normal or "").upper()
            self.target_mode_mismatch = bool(
                self.target_mode and self.normal_mode
                and self.target_mode != self.normal_mode)
            self.actual_mode_mismatch = bool(
                self.actual_mode and self.target_mode
                and self.actual_mode != self.target_mode)

        self.update()

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), self._color(Role.SURFACE_PANEL))
        self._paint_header(painter)
        self._paint_status_icons(painter)
        self._paint_state_commands(painter)
        self._paint_accept_and_modes(painter)
        self._paint_timers(painter)
        self._paint_device_and_failure(painter)
        self._paint_trend(painter)
        self._paint_actions(painter)
        painter.end()

    def _paint_header(self, painter: QPainter) -> None:
        self._text(painter, QRectF(35, 5, 180, 14), self.eyebrow,
                   FontRole.TINY, 9, Role.TEXT_DIM)
        self._text(painter, QRectF(35, 20, 180, 25), self.title,
                   FontRole.CHROME_BOLD, 17, Role.TEXT)
        self._text(painter, QRectF(35, 44, 180, 17), self.subtitle,
                   FontRole.UNIT, 10, Role.TEXT_DIM)
        self._text(painter, QRectF(56, 61, 139, 23), self.pv_text,
                   FontRole.VALUE, 17,
                   Role.ALARM_P1_TEXT if self.pv_bad else Role.HEADING)

    def _paint_status_icons(self, painter: QPainter) -> None:
        if self.interlocked is True:
            self._paint_interlocked(painter, self._STATUS_RECTS["interlocked"])
        if self.bypassed is True:
            self._paint_bypassed(painter, self._STATUS_RECTS["bypassed"])
        if self.no_permit is True:
            self._paint_no_permit(painter, self._STATUS_RECTS["no_permit"])
        if self.simulate_active is True:
            draw_faceplate_icon(
                painter, self._STATUS_RECTS["simulate"], "simulate")

    def _paint_state_commands(self, painter: QPainter) -> None:
        active = "cmd.start" if self.state_code in (1, 2) else (
            "cmd.stop" if self.state_code in (0, 3) else "")
        for key, caption in (("cmd.start", "START"),
                             ("cmd.stop", "STOP")):
            rect = self._COMMAND_RECTS[key]
            enabled = self._write_allowed(key)
            selected = active == key
            hovered = enabled and self._hover == key
            pressed = enabled and self._pressed == key
            painter.setPen(QPen(self._color(
                Role.ACTION if hovered or enabled and selected
                else Role.LINE if enabled else Role.LINE_SOFT), 1.0))
            painter.setBrush(QBrush(self._color(
                Role.SURFACE_SUNK if pressed else
                Role.ACTION if enabled and selected else
                Role.SURFACE_FIELD if enabled else Role.SURFACE_PANEL_ALT)))
            painter.drawRect(rect)
            self._text(
                painter, rect, caption, FontRole.CHROME_BOLD, 11,
                Role.SURFACE_PANEL if enabled and selected
                else Role.TEXT if enabled else Role.TEXT_FAINT)

    def _paint_accept_and_modes(self, painter: QPainter) -> None:
        box = QRectF(33, 188, 10, 10)
        painter.setPen(QPen(self._color(Role.LINE), 1))
        painter.setBrush(QBrush(self._color(Role.SURFACE_FIELD)))
        painter.drawRect(box)
        if self.accepted is True:
            painter.setPen(QPen(self._color(Role.ACTION_DEEP), 1.3))
            painter.drawLine(QPointF(35, 193), QPointF(38, 196))
            painter.drawLine(QPointF(38, 196), QPointF(42, 190))
        elif self.accepted is None:
            self._text(painter, box, "?", FontRole.TINY, 8, Role.TEXT_FAINT)
        self._text(painter, QRectF(47, 184, 66, 18), "Accept",
                   FontRole.LABEL, 10, Role.TEXT_DIM,
                   align=Qt.AlignmentFlag.AlignLeft)

        self._mode_row(
            painter, 183, "Target mode", self.target_mode,
            self.target_mode_mismatch)
        self._mode_row(
            painter, 202, "Actual mode", self.actual_mode,
            self.actual_mode_mismatch)

    def _mode_row(self, painter: QPainter, y: float, _caption: str,
                  value: str, mismatch: bool) -> None:
        if mismatch:
            rect = QRectF(148, y + 2, 13, 14)
            painter.fillRect(rect, self._color(Role.HEADING))
            self._text(painter, rect, "!", FontRole.CHROME_BOLD, 10,
                       Role.SURFACE_PANEL)
        self._text(painter, QRectF(164, y, 78, 18), value or "\u2014",
                   FontRole.VALUE_SMALL, 11,
                   Role.ALARM_P2_TEXT if mismatch else Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)
        # Captions live outside these rows in the annotated reference; they
        # are intentionally not painted twice into the narrow runtime shell.

    def _paint_timers(self, painter: QPainter) -> None:
        self._text(painter, QRectF(30, 218, 125, 18), "State Transition",
                   FontRole.CHROME_BOLD, 10, Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)
        self._labeled_value(
            painter, 237, "Time Limit", self._seconds(self.transition_limit))
        self._labeled_value(
            painter, 263, "Elapsed Time", self._seconds(self.elapsed))

    def _paint_device_and_failure(self, painter: QPainter) -> None:
        self._text(painter, QRectF(30, 288, 120, 18), "Device State",
                   FontRole.CHROME_BOLD, 10, Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(30, 307, 139, 22), self.state_name,
                   FontRole.VALUE_SMALL, 10,
                   Role.ALARM_P2_TEXT if self.state_code in (4, 5, 6)
                   else Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)
        if self.reset_visible:
            rect = self._RESET_RECT
            enabled = self._write_allowed("cmd.reset")
            painter.setPen(QPen(self._color(
                Role.ACTION if enabled and self._hover == "cmd.reset"
                else Role.LINE if enabled else Role.LINE_SOFT), 1))
            painter.setBrush(QBrush(self._color(
                Role.SURFACE_SUNK
                if enabled and self._pressed == "cmd.reset"
                else Role.SURFACE_FIELD if enabled
                else Role.SURFACE_PANEL_ALT)))
            painter.drawRect(rect)
            self._text(painter, rect, "Reset", FontRole.CHROME_BOLD, 10,
                       Role.TEXT if enabled else Role.TEXT_FAINT)
        self._labeled_value(
            painter, 336, "Delayed",
            "HELD" if self.paused is True
            else self._seconds(self.delay_remaining))

        self._text(painter, QRectF(30, 358, 120, 18), "Fail Condition",
                   FontRole.CHROME_BOLD, 10, Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(30, 376, 195, 20), self.failure_text,
                   FontRole.VALUE_SMALL, 10,
                   Role.ALARM_P1_TEXT if self.fail_active is True
                   else Role.TEXT,
                   align=Qt.AlignmentFlag.AlignLeft)

    def _paint_trend(self, painter: QPainter) -> None:
        outer = self._TREND_RECT
        painter.setPen(QPen(self._color(Role.LINE_SOFT), 1))
        painter.setBrush(QBrush(self._color(Role.SURFACE_SUNK)))
        painter.drawRect(outer)
        plot = outer.adjusted(25, 8, -2, -10)
        painter.setPen(QPen(self._color(Role.LINE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plot)

        for value, fraction in ((6, 0.0), (4, 1 / 3),
                                (2, 2 / 3), (0, 1.0)):
            y = plot.top() + plot.height() * fraction
            self._text(painter, QRectF(0, y - 7, 47, 14), str(value),
                       FontRole.TINY, 8, Role.HEADING,
                       align=Qt.AlignmentFlag.AlignRight)
        values = list(self._trend)
        if len(values) > 1:
            path = QPainterPath()
            for index, value in enumerate(values):
                x = plot.left() + plot.width() * index / (len(values) - 1)
                y = plot.bottom() - max(0.0, min(1.0, value / 6.0)) \
                    * plot.height()
                path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
            painter.setPen(QPen(self._color(Role.BAR_PV), 1.2))
            painter.drawPath(path)

    def _paint_actions(self, painter: QPainter) -> None:
        for action, rect in self._ACTION_RECTS.items():
            if action not in self._available_actions:
                continue
            icon = "module_detail" if action == "detail" \
                else "faceplate_return"
            draw_faceplate_icon(
                painter, rect, icon, button=True,
                pressed=self._pressed == f"action:{action}", enabled=True)

    def _labeled_value(self, painter: QPainter, y: float,
                       caption: str, value: str) -> None:
        self._text(painter, QRectF(39, y, 92, 18), caption,
                   FontRole.LABEL, 10, Role.TEXT_DIM,
                   align=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(132, y, 95, 18), value,
                   FontRole.VALUE_SMALL, 10, Role.TEXT,
                   align=Qt.AlignmentFlag.AlignRight)

    def _paint_interlocked(self, painter: QPainter, rect: QRectF) -> None:
        center = rect.center()
        polygon = QPolygonF([
            QPointF(center.x(), rect.top()),
            QPointF(rect.right(), center.y()),
            QPointF(center.x(), rect.bottom()),
            QPointF(rect.left(), center.y()),
        ])
        painter.setPen(QPen(self._color(Role.TEXT_FAINT), 1))
        painter.setBrush(QBrush(self._color(Role.SURFACE_FIELD)))
        painter.drawPolygon(polygon)
        painter.setPen(QPen(self._color(Role.TEXT_DIM), 1.4))
        painter.drawLine(QPointF(center.x(), rect.top() + 4),
                         QPointF(center.x(), rect.bottom() - 4))
        painter.drawLine(QPointF(center.x() - 3, rect.top() + 7),
                         QPointF(center.x(), rect.top() + 4))
        painter.drawLine(QPointF(center.x() + 3, rect.top() + 7),
                         QPointF(center.x(), rect.top() + 4))
        painter.drawLine(QPointF(center.x() - 3, rect.bottom() - 7),
                         QPointF(center.x(), rect.bottom() - 4))
        painter.drawLine(QPointF(center.x() + 3, rect.bottom() - 7),
                         QPointF(center.x(), rect.bottom() - 4))

    def _paint_bypassed(self, painter: QPainter, rect: QRectF) -> None:
        center = rect.center()
        polygon = QPolygonF([
            QPointF(center.x(), rect.top()),
            QPointF(rect.right(), center.y()),
            QPointF(center.x(), rect.bottom()),
            QPointF(rect.left(), center.y()),
        ])
        painter.setPen(QPen(self._color(Role.HEADING), 1))
        painter.setBrush(QBrush(self._color(Role.HEADING)))
        painter.drawPolygon(polygon)
        painter.setPen(QPen(self._color(Role.SURFACE_PANEL), 1.4))
        painter.drawLine(QPointF(rect.left() + 4, center.y()),
                         QPointF(rect.right() - 4, center.y()))
        painter.drawLine(QPointF(rect.right() - 7, center.y() - 3),
                         QPointF(rect.right() - 4, center.y()))
        painter.drawLine(QPointF(rect.right() - 7, center.y() + 3),
                         QPointF(rect.right() - 4, center.y()))

    def _paint_no_permit(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QPen(self._color(Role.TEXT_FAINT), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(2, 2, -2, -2))
        painter.drawLine(rect.topRight() + QPointF(-3, 3),
                         rect.bottomLeft() + QPointF(3, -3))

    # ------------------------------------------------------------- interaction
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hit = self._hit(event.position())
        if hit != self._hover:
            self._hover = hit
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if hit else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._pressed = self._hit(event.position())
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        released = self._hit(event.position())
        pressed = self._pressed
        self._pressed = None
        if pressed and released == pressed:
            if pressed.startswith("cmd."):
                self.write_requested.emit(pressed, True)
            elif pressed.startswith("action:"):
                self.action_requested.emit(pressed.partition(":")[2])
        self.update()
        event.accept()

    def _hit(self, point: QPointF) -> str | None:
        for key, rect in self._COMMAND_RECTS.items():
            if self._write_allowed(key) and rect.contains(point):
                return key
        if self.reset_visible and self._write_allowed("cmd.reset") \
                and self._RESET_RECT.contains(point):
            return "cmd.reset"
        for action, rect in self._ACTION_RECTS.items():
            if action in self._available_actions and rect.contains(point):
                return f"action:{action}"
        return None

    # ---------------------------------------------------------------- helpers
    def _color(self, role: Role) -> QColor:
        return self._colors[role]

    def _text(self, painter: QPainter, rect: QRectF, text: str,
              role: FontRole, size: int, color: Role, *,
              align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter) -> None:
        painter.save()
        painter.setFont(font_for(role, size))
        painter.setPen(self._color(color))
        painter.drawText(
            rect,
            int(align | Qt.AlignmentFlag.AlignVCenter),
            str(text),
        )
        painter.restore()

    @staticmethod
    def _seconds(value: float | None) -> str:
        return "\u2014" if value is None else f"{value:.1f} s"


WHOLE_SURFACES = {"DeviceFaceplate": DeviceFaceplateSurface}


__all__ = ["DeviceFaceplateSurface", "WHOLE_SURFACES"]
