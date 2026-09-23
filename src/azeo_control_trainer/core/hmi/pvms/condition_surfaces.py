"""Measured Azeo AT_fp and DCC_fp condition-table surfaces.

The vendor figures use two fixed, non-reflowing coordinate systems:

* AT_fp is 578 x 449 logical pixels;
* DCC_fp is 624 x 505 logical pixels.

Putting either table through ``QTableWidget`` changed the column widths with
the platform font, grew the body from its size hint, and made the first-out
arrow look like an ordinary character.  These painters keep every column and
row on the figure's measured coordinates while the live condition document
continues to come from the block's one ``condition_table()`` implementation.

The DCC surface is used for ``MotorInterlockFaceplate``.  Its third tab says
``Trips`` because this trainer's motor interlock has trip conditions where a
Azeo DCC block has Force Setpoints.  That is a vocabulary adaptation, not a
semantic one: permissive/interlock True means satisfied, while trip True
means ACTIVE.  Keeping that inversion here prevents a tripped condition from
being painted as OK.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..theme.fonts import FontRole, font_for
from ..theme.roles import Role
from .faceplate_icons import draw_faceplate_icon


def _result(bound, key):
    binding = (bound or {}).get(key)
    if binding is None or isinstance(binding, tuple):
        return None
    return getattr(binding, "result", None)


def _value(bound, key, default=None):
    result = _result(bound, key)
    return default if result is None else result.value


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _condition_document(bound) -> list[dict]:
    """Decode the shared CONDITIONS result without inventing healthy rows."""
    result = _result(bound, "conditions")
    if result is None or getattr(getattr(result, "quality", None),
                                 "name", "BAD") == "BAD":
        return []
    document = result.value
    if isinstance(document, str):
        try:
            document = json.loads(document or "[]")
        except (TypeError, ValueError):
            return []
    if isinstance(document, dict):
        document = document.get("rows", [])
    if not isinstance(document, (list, tuple)):
        return []
    return [dict(row) for row in document if isinstance(row, dict)]


@dataclass(frozen=True, slots=True)
class ConditionRow:
    """One normalized row shared by the two measured surfaces."""

    kind: str
    description: str
    state: bool
    delay_on: float
    delay_off: float
    timer: float
    value: object
    target_state: str
    bypassed: bool
    reset_required: bool
    hold_manual: bool
    first_out: bool

    @classmethod
    def from_document(cls, source: dict) -> "ConditionRow":
        kind = str(source.get("kind", "condition") or "condition").lower()
        kind = kind.replace(" ", "_").replace("-", "_")
        delay = _as_float(source.get("delay", 0.0))
        # MOTOR_INTERLOCK publishes one delay with the polarity in its kind:
        # permissives count toward True; run interlocks count toward False.
        delay_on = _as_float(source.get(
            "delay_on", delay if kind == "permissive" else 0.0))
        delay_off = _as_float(source.get(
            "delay_off", delay if kind in ("interlock", "trip") else 0.0))
        return cls(
            kind=kind,
            description=str(source.get("description")
                            or source.get("name") or ""),
            state=bool(source.get("state", False)),
            delay_on=delay_on,
            delay_off=delay_off,
            timer=max(0.0, _as_float(source.get("timer", 0.0))),
            value=source.get("value", source.get("track_value")),
            target_state=str(source.get("target_state")
                             or source.get("state_name") or ""),
            bypassed=bool(source.get("bypassed", False)),
            reset_required=bool(source.get("reset_required", False)),
            hold_manual=bool(source.get("hold_manual", False)),
            first_out=bool(source.get("first_out", False)),
        )

    @property
    def healthy(self) -> bool:
        """Motor condition polarity, including the easy-to-miss trip inverse."""
        return (not self.state) if self.kind == "trip" else self.state

    @property
    def active(self) -> bool:
        """Whether this row demands operator attention."""
        return not self.healthy

    @property
    def state_text(self) -> str:
        if self.target_state:
            return self.target_state
        if self.kind == "trip":
            return "ACTIVE" if self.state else "OK"
        return "OK" if self.state else "NOT MET"

    @property
    def configured_delay(self) -> float:
        # The timer progresses toward the edge relevant to the current state.
        # Fall back to the other edge because the motor adapter exposes only
        # one side, and a zero-delay row has no progress at all.
        preferred = self.delay_on if self.state else self.delay_off
        return preferred or self.delay_on or self.delay_off

    @property
    def timer_fraction(self) -> float | None:
        delay = self.configured_delay
        if delay <= 0.0:
            return None
        return max(0.0, min(1.0, self.timer / delay))


class _ConditionSurface(QWidget):
    """Common fixed-coordinate furniture and interaction."""

    WHOLE_SURFACE = True
    write_requested = Signal(str, object)
    action_requested = Signal(str)

    DESIGN_WIDTH = 0.0
    DESIGN_HEIGHT = 0.0
    RETURN_RECT = QRectF()
    RESET_RECT = QRectF()
    MAX_ROWS = 16

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.rows: list[ConditionRow] = []
        self.identity_path = ""
        self.identity_title = ""
        self.identity_zone = ""
        self._reset_visible = False
        self._available_actions: set[str] = set()
        self._write_permissions: frozenset[str] | None = None
        self._return_intent = "faceplate"
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

    def set_identity(self, path: str, description: str = "") -> None:
        module, separator, block = str(path).partition("/")
        self.identity_path = str(path)
        self.identity_zone = block if separator else ""
        self.identity_title = module or description
        self.update()

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.background = QColor(palette[Role.SURFACE_PANEL])
        self.surface_alt = QColor(palette[Role.SURFACE_PANEL_ALT])
        self.field = QColor(palette[Role.SURFACE_FIELD])
        self.text = QColor(palette[Role.TEXT])
        self.text_dim = QColor(palette[Role.TEXT_DIM])
        self.text_faint = QColor(palette[Role.TEXT_FAINT])
        self.line = QColor(palette[Role.LINE])
        self.line_soft = QColor(palette[Role.LINE_SOFT])
        self.action = QColor(palette[Role.ACTION])
        self.alarm = QColor(palette[Role.ALARM_P1_TEXT])
        # Azeo uses a fixed magenta-red first-out arrow; it is recognition
        # furniture, not an alarm-priority colour supplied by the theme.
        self.first_out_colour = QColor("#CE0F45")
        self.update()

    def set_available_actions(self, keys) -> None:
        """Advertise the module-faceplate return only when it has a target."""
        offered = {str(key) for key in (keys or ())}
        self._available_actions = offered.intersection({"faceplate"})
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

    def refresh(self, bound) -> None:
        self.rows = [ConditionRow.from_document(row)
                     for row in _condition_document(bound)][:self.MAX_ROWS]
        reset_claim = bool(_value(bound, "reset.required", False))
        self._reset_visible = reset_claim or any(
            row.reset_required and not row.state for row in self.rows)
        self.update()

    @property
    def reset_visible(self) -> bool:
        return self._reset_visible

    @property
    def first_out_rows(self) -> tuple[ConditionRow, ...]:
        return tuple(row for row in self.rows if row.first_out)

    def _transform(self) -> tuple[float, float, float]:
        scale = min(self.width() / self.DESIGN_WIDTH,
                    self.height() / self.DESIGN_HEIGHT)
        return (scale,
                (self.width() - self.DESIGN_WIDTH * scale) / 2.0,
                (self.height() - self.DESIGN_HEIGHT * scale) / 2.0)

    def _design_point(self, point: QPointF) -> QPointF:
        scale, offset_x, offset_y = self._transform()
        return QPointF((point.x() - offset_x) / scale,
                       (point.y() - offset_y) / scale)

    @staticmethod
    def _font(pixels: int, *, bold=False):
        # font_for owns and caches the vendored face.  Do not mutate it: the
        # same cached object is deliberately shared by every painted surface.
        role = FontRole.CHROME_BOLD if bold else FontRole.LABEL
        return font_for(role, pixels)

    def _text(self, painter: QPainter, rect: QRectF, text: object,
              pixels: int = 12, colour: QColor | None = None, *,
              bold=False, alignment=Qt.AlignmentFlag.AlignCenter) -> None:
        painter.setFont(self._font(pixels, bold=bold))
        painter.setPen(colour or self.text)
        painter.drawText(rect, alignment | Qt.AlignmentFlag.AlignVCenter,
                         str(text))

    def _identity(self, painter: QPainter) -> None:
        self._text(painter, QRectF(100, 5, self.DESIGN_WIDTH - 200, 16),
                   self.identity_zone or "", 12, self.text_dim)
        self._text(painter, QRectF(70, 23, self.DESIGN_WIDTH - 140, 25),
                   self.identity_title or "", 18, self.text, bold=True)

    @staticmethod
    def _number(value, *, hashes="####") -> str:
        if value is None:
            return hashes
        if isinstance(value, bool):
            return "1" if value else "0"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return f"{number:.0f}"
        return f"{number:.2f}".rstrip("0").rstrip(".")

    def _checkbox(self, painter: QPainter, rect: QRectF,
                  checked: bool, *, enabled=True) -> None:
        edge = self.line if enabled else self.line_soft
        painter.setPen(QPen(edge, 0.8))
        painter.setBrush(self.field if enabled else self.surface_alt)
        painter.drawRect(rect)
        if not checked:
            return
        painter.setPen(QPen(self.text if enabled else self.text_dim, 1.0,
                            Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(
            QPointF(rect.left() + 1.6, rect.center().y()),
            QPointF(rect.left() + 3.3, rect.bottom() - 1.4),
        )
        painter.drawLine(
            QPointF(rect.left() + 3.3, rect.bottom() - 1.4),
            QPointF(rect.right() - 1.3, rect.top() + 1.4),
        )

    def _first_out_arrow(self, painter: QPainter, rect: QRectF) -> None:
        centre_y = rect.center().y()
        path = QPainterPath(QPointF(rect.left(), centre_y - 2.6))
        path.lineTo(rect.right() - 9.0, centre_y - 2.6)
        path.lineTo(rect.right() - 9.0, centre_y - 6.0)
        path.lineTo(rect.right(), centre_y)
        path.lineTo(rect.right() - 9.0, centre_y + 6.0)
        path.lineTo(rect.right() - 9.0, centre_y + 2.6)
        path.lineTo(rect.left(), centre_y + 2.6)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.first_out_colour)
        painter.drawPath(path)

    def _reset_button(self, painter: QPainter) -> None:
        if not self._reset_visible:
            return
        enabled = self._write_allowed("cmd.reset")
        hover = enabled and self._hover == "reset"
        pressed = enabled and self._pressed == "reset"
        painter.setPen(QPen(
            self.action if hover else self.line if enabled else self.line_soft,
            1.0))
        painter.setBrush(
            self.surface_alt if pressed or not enabled else self.field)
        painter.drawRect(self.RESET_RECT)
        self._text(painter, self.RESET_RECT, "Reset", 14,
                   self.text if enabled else self.text_faint,
                   bold=True)

    def _return_button(self, painter: QPainter) -> None:
        if not self._available_actions:
            return
        draw_faceplate_icon(
            painter,
            self.RETURN_RECT,
            "faceplate_return",
            button=True,
            pressed=self._pressed == "return",
            enabled=True,
        )

    def _hit(self, point: QPointF) -> str | None:
        if self._available_actions and self.RETURN_RECT.contains(point):
            return "return"
        if self._reset_visible and self._write_allowed("cmd.reset") \
                and self.RESET_RECT.contains(point):
            return "reset"
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hit = self._hit(self._design_point(event.position()))
        if hit != self._hover:
            self._hover = hit
            self.setCursor(Qt.CursorShape.PointingHandCursor
                           if hit else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._pressed = self._hit(self._design_point(event.position()))
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        released = self._hit(self._design_point(event.position()))
        pressed = self._pressed
        self._pressed = None
        if pressed and pressed == released:
            if pressed == "reset":
                self.write_requested.emit("cmd.reset", True)
            elif pressed == "return":
                self.action_requested.emit(self._return_intent)
        self.update()
        event.accept()


class ATFaceplateSurface(_ConditionSurface):
    """AT_fp in the manual's measured 578 x 449 coordinate system."""

    DESIGN_WIDTH = 578.0
    DESIGN_HEIGHT = 449.0
    RESET_RECT = QRectF(20, 19, 54, 28)
    RETURN_RECT = QRectF(532, 411, 33, 33)
    ROW_TOP = 86.0
    ROW_HEIGHT = 19.5

    # Exposed for image-independent regression checks.  A platform font may
    # change glyph metrics, but it must never move a process value into the
    # wrong meaning-bearing column.
    COLUMN_CENTRES = {
        "first_out": 28.0,
        "condition": 145.0,
        "delay_on": 262.0,
        "delay_off": 306.0,
        "timer": 350.0,
        "value": 397.0,
        "bypass": 441.0,
        "reset_required": 493.0,
        "hold_manual": 546.0,
    }

    def refresh(self, bound) -> None:
        super().refresh(bound)
        # AT implementations may publish a dedicated document while the
        # generic source still uses CONDITIONS for all condition-bearing
        # blocks.  Prefer the dedicated result only when it is actually good.
        dedicated = _result(bound, "tracking.conditions")
        if dedicated is not None and getattr(
                getattr(dedicated, "quality", None), "name", "BAD") != "BAD":
            proxy = type("_Binding", (), {"result": dedicated})()
            self.rows = [ConditionRow.from_document(row) for row in
                         _condition_document({"conditions": proxy})][
                             :self.MAX_ROWS]
            self._reset_visible = bool(_value(
                bound, "reset.required", False)) or any(
                    row.reset_required and not row.state for row in self.rows)
        if not self.rows:
            # This trainer's compact AT block exposes one real tracking
            # request rather than Azeo's sixteen-condition document.  Keep
            # the documented table anatomy, but populate it from that actual
            # contract instead of either drawing an empty 449-pixel shell or
            # inventing sixteen healthy placeholder rows.
            request = _result(bound, "request")
            active = _result(bound, "state.active")
            tracked = _result(bound, "pv.value")
            if request is not None and getattr(
                    getattr(request, "quality", None), "name", "BAD") != "BAD":
                state = bool(active.value) if active is not None else bool(
                    request.value)
                self.rows = [ConditionRow(
                    kind="tracking",
                    description="Tracking request",
                    state=state,
                    delay_on=0.0,
                    delay_off=0.0,
                    timer=0.0,
                    value=None if tracked is None else tracked.value,
                    target_state="",
                    bypassed=False,
                    reset_required=False,
                    hold_manual=False,
                    first_out=False,
                )]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        scale, offset_x, offset_y = self._transform()
        painter.fillRect(self.rect(), self.background)
        painter.translate(offset_x, offset_y)
        painter.scale(scale, scale)
        painter.fillRect(QRectF(0, 0, self.DESIGN_WIDTH,
                                self.DESIGN_HEIGHT), self.background)
        self._identity(painter)
        self._headers(painter)
        self._draw_rows(painter)
        self._reset_button(painter)
        self._return_button(painter)

    def _headers(self, painter: QPainter) -> None:
        self._text(painter, QRectF(5, 50, 48, 34), "First\nOut", 12,
                   self.text, alignment=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(72, 57, 147, 22), "Condition", 12,
                   self.text, alignment=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(238, 49, 48, 34), "Delay\nOn", 12,
                   self.text)
        self._text(painter, QRectF(282, 49, 48, 34), "Delay\nOff", 12,
                   self.text)
        self._text(painter, QRectF(329, 57, 42, 22), "Timer", 12,
                   self.text)
        self._text(painter, QRectF(376, 57, 42, 22), "Value", 12,
                   self.text)
        self._text(painter, QRectF(421, 57, 43, 22), "Bypass", 12,
                   self.text)
        self._text(painter, QRectF(469, 47, 50, 38), "Reset\nRequired",
                   12, self.text)
        self._text(painter, QRectF(521, 47, 50, 38), "Hold in\nManual",
                   12, self.text)

    def _draw_rows(self, painter: QPainter) -> None:
        for index, row in enumerate(self.rows[:self.MAX_ROWS]):
            top = self.ROW_TOP + index * self.ROW_HEIGHT
            colour = self.text if row.state or row.first_out else self.text_dim
            if row.first_out:
                self._first_out_arrow(painter, QRectF(11, top + 3, 36, 13))
            self._text(painter, QRectF(50, top, 187, self.ROW_HEIGHT),
                       row.description, 12, colour,
                       alignment=Qt.AlignmentFlag.AlignLeft)
            self._text(painter, QRectF(240, top, 44, self.ROW_HEIGHT),
                       self._number(row.delay_on), 12, colour)
            self._text(painter, QRectF(284, top, 44, self.ROW_HEIGHT),
                       self._number(row.delay_off), 12, colour)
            self._text(painter, QRectF(330, top, 40, self.ROW_HEIGHT),
                       self._number(row.timer), 12, colour)
            self._text(painter, QRectF(375, top, 44, self.ROW_HEIGHT),
                       self._number(row.value), 12, colour)
            self._checkbox(painter, QRectF(436, top + 6, 8, 8),
                           row.bypassed)
            self._checkbox(painter, QRectF(489, top + 6, 8, 8),
                           row.reset_required, enabled=False)
            self._checkbox(painter, QRectF(542, top + 6, 8, 8),
                           row.hold_manual)


class MotorInterlockFaceplateSurface(_ConditionSurface):
    """DCC_fp adapted to the motor interlock's three real condition kinds."""

    DESIGN_WIDTH = 624.0
    DESIGN_HEIGHT = 505.0
    # Keep the reset command inside the table header.  The previous 28-pixel
    # button extended four pixels into the first condition row.
    RESET_RECT = QRectF(62, 79, 56, 22)
    RETURN_RECT = QRectF(588, 470, 33, 33)
    TABLE_RECT = QRectF(27, 67, 573, 403)
    ROW_TOP = 103.0
    # Sixteen rows terminate exactly at the measured table border instead of
    # painting the last baseline one pixel beyond the clipped surface.
    ROW_HEIGHT = (TABLE_RECT.bottom() - ROW_TOP) / _ConditionSurface.MAX_ROWS
    TAB_RECTS = (
        QRectF(27, 47, 93, 20),
        QRectF(120, 47, 93, 20),
        QRectF(213, 47, 94, 20),
        QRectF(307, 47, 26, 20),
    )
    TAB_KEYS = ("interlock", "permissive", "trip")
    TAB_TITLES = ("Interlocks", "Permissives", "Trips")

    COLUMN_CENTRES = {
        "first_out": 47.0,
        "condition": 164.0,
        "delay_on": 273.0,
        "delay_off": 315.0,
        "timer": 357.0,
        "state": 420.0,
        "bypass": 501.0,
        "reset_required": 547.0,
    }

    def __init__(self, palette: dict, parent=None) -> None:
        self.active_tab = "interlock"
        super().__init__(palette, parent)

    @property
    def tab_titles(self) -> tuple[str, ...]:
        return self.TAB_TITLES

    def set_active_tab(self, tab: int | str) -> bool:
        if isinstance(tab, int):
            if not 0 <= tab < len(self.TAB_KEYS):
                return False
            key = self.TAB_KEYS[tab]
        else:
            key = str(tab).lower()
            aliases = {"force": "trip", "force_setpoint": "trip",
                       "force_setpoints": "trip"}
            key = aliases.get(key, key)
            if key not in self.TAB_KEYS:
                return False
        if key == self.active_tab:
            return False
        self.active_tab = key
        self.update()
        return True

    def rows_for(self, kind: str) -> tuple[ConditionRow, ...]:
        normalized = str(kind).lower().replace(" ", "_")
        if normalized == "trip":
            accepted = {"trip", "force", "force_setpoint",
                        "force_setpoints"}
            return tuple(row for row in self.rows if row.kind in accepted)
        return tuple(row for row in self.rows if row.kind == normalized)

    @property
    def visible_rows(self) -> tuple[ConditionRow, ...]:
        return self.rows_for(self.active_tab)[:self.MAX_ROWS]

    def _hit(self, point: QPointF) -> str | None:
        hit = super()._hit(point)
        if hit:
            return hit
        for index, rect in enumerate(self.TAB_RECTS[:3]):
            if rect.contains(point):
                return f"tab_{index}"
        return None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            released = self._hit(self._design_point(event.position()))
            pressed = self._pressed
            if pressed and pressed == released and pressed.startswith("tab_"):
                self._pressed = None
                self.set_active_tab(int(pressed.rsplit("_", 1)[1]))
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        scale, offset_x, offset_y = self._transform()
        painter.fillRect(self.rect(), self.background)
        painter.translate(offset_x, offset_y)
        painter.scale(scale, scale)
        painter.fillRect(QRectF(0, 0, self.DESIGN_WIDTH,
                                self.DESIGN_HEIGHT), self.background)
        self._identity(painter)
        self._tabs(painter)
        painter.setPen(QPen(self.line_soft, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.TABLE_RECT)
        self._headers(painter)
        self._draw_rows(painter)
        self._reset_button(painter)
        self._return_button(painter)

    def _tabs(self, painter: QPainter) -> None:
        for index, (rect, title) in enumerate(zip(
                self.TAB_RECTS[:3], self.TAB_TITLES)):
            selected = self.TAB_KEYS[index] == self.active_tab
            painter.setPen(QPen(self.line, 0.8))
            painter.setBrush(self.surface_alt if selected else self.background)
            painter.drawRect(rect)
            self._text(painter, rect, title, 10,
                       self.text if selected else self.text_dim)
        self._text(painter, self.TAB_RECTS[3], "+", 12, self.text,
                   alignment=Qt.AlignmentFlag.AlignLeft)

    def _headers(self, painter: QPainter) -> None:
        if self.active_tab == "interlock":
            self._text(painter, QRectF(34, 69, 33, 35), "First\nOut", 12,
                       self.text, alignment=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(120, 82, 103, 21), "Condition", 12,
                   self.text, alignment=Qt.AlignmentFlag.AlignLeft)
        self._text(painter, QRectF(250, 69, 45, 35), "Delay\nOn", 12,
                   self.text)
        if self.active_tab != "trip":
            self._text(painter, QRectF(293, 69, 45, 35), "Delay\nOff", 12,
                       self.text)
        self._text(painter, QRectF(338, 82, 39, 21), "Timer", 12,
                   self.text)
        if self.active_tab in ("interlock", "trip"):
            self._text(painter, QRectF(385, 82, 70, 21), "State", 12,
                       self.text)
        self._text(painter, QRectF(475, 82, 51, 21), "Bypass", 12,
                   self.text)
        if self.active_tab == "interlock":
            self._text(painter, QRectF(530, 69, 54, 35),
                       "Reset\nRequired", 12, self.text)

    def _draw_rows(self, painter: QPainter) -> None:
        for index, row in enumerate(self.visible_rows):
            top = self.ROW_TOP + index * self.ROW_HEIGHT
            colour = self.text if row.active or row.first_out else self.text_dim
            if row.first_out and self.active_tab == "interlock":
                self._first_out_arrow(painter, QRectF(36, top + 4, 34, 13))
            self._text(painter, QRectF(72, top, 183, self.ROW_HEIGHT),
                       row.description, 12, colour,
                       alignment=Qt.AlignmentFlag.AlignLeft)
            self._text(painter, QRectF(258, top, 40, self.ROW_HEIGHT),
                       self._number(row.delay_on), 12, colour)
            if self.active_tab != "trip":
                self._text(painter, QRectF(300, top, 40, self.ROW_HEIGHT),
                           self._number(row.delay_off), 12, colour)
            self._text(painter, QRectF(341, top, 38, self.ROW_HEIGHT),
                       self._number(row.timer), 12, colour)
            if self.active_tab in ("interlock", "trip"):
                state_colour = self.alarm if row.active else colour
                self._text(painter, QRectF(382, top, 96, self.ROW_HEIGHT),
                           row.state_text, 12, state_colour,
                           alignment=Qt.AlignmentFlag.AlignLeft)
            self._checkbox(painter, QRectF(496, top + 8, 8, 8),
                           row.bypassed)
            if self.active_tab == "interlock":
                self._checkbox(painter, QRectF(544, top + 8, 8, 8),
                               row.reset_required, enabled=False)


# Render-side integration imports this one mapping.  Keeping it here avoids a
# second list of names drifting from the classes it constructs.
CONDITION_FACEPLATE_VISUALS = {
    "ATFaceplate": ATFaceplateSurface,
    "MotorInterlockFaceplate": MotorInterlockFaceplateSurface,
}
WHOLE_SURFACES = CONDITION_FACEPLATE_VISUALS


__all__ = [
    "ATFaceplateSurface",
    "CONDITION_FACEPLATE_VISUALS",
    "ConditionRow",
    "MotorInterlockFaceplateSurface",
    "WHOLE_SURFACES",
]
