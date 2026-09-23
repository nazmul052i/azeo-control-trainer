"""Measured Azeo function-block faceplate surfaces.

The extracted ``PVMS+FB`` figures use fixed plates rather than a stack of Qt
widgets.  The distinction matters: a table's size hint must not move the
identity band or stretch the tab strip from one module to the next.  These
painters therefore work in the measured coordinate systems of Xmtr_fp,
ECTLSL_fp, AVTR_fp, DVTR_fp, SEQ_fp and STD_fp.

The licensed reference images are a specification only; deployed stations do
not load them.  Every mark is code-native, and every process value comes from
the supplied binding map.  A missing binding stays visibly absent instead of
being replaced with a plausible healthy value.
"""
from __future__ import annotations

from collections.abc import Iterable
import json
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from azeo_control_trainer.core.strategy.model.terminal import Quality
from ..binding.result import UNRESOLVED
from ..theme.fonts import FontRole, font_for
from ..theme.roles import Role
from .faceplate_icons import draw_faceplate_icon
from .faceplate_ui import NO_VALUE, result_of


def _available(bound, key: str) -> bool:
    result = result_of(bound, key)
    return result is not UNRESOLVED and result.value is not None


def _raw(bound, key: str, default=None):
    result = result_of(bound, key)
    if result is UNRESOLVED or result.value is None:
        return default
    return result.value


def _truth(bound, key: str) -> bool:
    return bool(_raw(bound, key, False))


def _units(bound, key: str) -> str:
    result = result_of(bound, key)
    if result is UNRESOLVED:
        return ""
    return str(getattr(result, "units", "") or "")


def _bounded_count(bound, key: str, default: int, maximum: int) -> int:
    """Read an extensible-row count without widening absent contracts."""
    try:
        value = int(_raw(bound, key, default))
    except (TypeError, ValueError):
        value = default
    return max(0, min(maximum, value))


def _value_text(bound, key: str, *, decimals: int = 1,
                missing: str = NO_VALUE) -> str:
    """Format one process value without turning absent data into zero."""
    result = result_of(bound, key)
    if result is UNRESOLVED or result.value is None \
            or result.quality is Quality.BAD:
        return missing
    value = result.value
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _selector_value_text(bound, key: str, *, decimals: int = 2) -> str:
    """Format a selector input without publishing its unused sentinel.

    The trainer's three-input MIN/MAX blocks deliberately use positive or
    negative infinity for unconnected inputs.  Those values make the
    algorithm ignore the inputs; they are not process measurements and must
    never leak into the operator surface as ``inf``.
    """
    result = result_of(bound, key)
    if result is UNRESOLVED or result.value is None \
            or result.quality is Quality.BAD:
        return NO_VALUE
    value = result.value
    if isinstance(value, float) and not math.isfinite(value):
        return "Not used"
    return _value_text(bound, key, decimals=decimals)


class MeasuredFaceplateSurface(QWidget):
    """Shared fixed-coordinate plate and its truthful interaction boundary."""

    DESIGN_WIDTH = 250
    DESIGN_HEIGHT = 412
    RETURN_RECT = QRectF(0, 0, 0, 0)
    # Render owns the outer contextual title strip; this widget owns every
    # pixel below it.  Mixing these painters with generic Section size hints
    # recreates the displaced rows these fixed surfaces were introduced to
    # eliminate.
    WHOLE_SURFACE = True

    action_requested = Signal(str)
    write_requested = Signal(str, object)

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._bound = {}
        self._identity_path = ""
        self._identity_description = ""
        self._hover = ""
        self._pressed = ""
        self._available_actions: set[str] = set()
        self._write_permissions: frozenset[str] | None = None
        self._return_intent = "faceplate"
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(self.DESIGN_WIDTH, self.DESIGN_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.DESIGN_WIDTH, self.DESIGN_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def colour(self, role: Role) -> QColor:
        return QColor(self._palette[role])

    def set_identity(self, path: str, description: str = "") -> None:
        self._identity_path = str(path or "")
        self._identity_description = str(description or "")
        self.update()

    def refresh(self, bound) -> None:
        self._bound = bound or {}
        self.update()

    def apply_theme(self, palette: dict) -> None:
        """Re-skin an open measured plate without rebuilding its state."""
        self._palette = palette
        self.update()

    def set_available_actions(self, keys) -> None:
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
        """Actions whose host has explicitly advertised a real handler."""
        return frozenset(self._available_actions)

    @property
    def identity_lines(self) -> tuple[str, str]:
        """The two lines the manual reserves above every FB faceplate."""
        path = self._identity_path
        return path, self._identity_description or path or "UNBOUND"

    def _font(self, role: FontRole, size: int):
        return font_for(role, size)

    def _text(self, painter: QPainter, rect: QRectF, text: object,
              role: Role = Role.TEXT, *, size: int = 11,
              align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
              bold: bool = False, elide: bool = False) -> None:
        font_role = FontRole.CHROME_BOLD if bold else FontRole.CHROME
        font = self._font(font_role, size)
        painter.setFont(font)
        painter.setPen(self.colour(role))
        output = str(text)
        if elide:
            output = painter.fontMetrics().elidedText(
                output, Qt.TextElideMode.ElideRight, max(0, int(rect.width())))
        painter.drawText(rect, int(align), output)

    def _mono(self, painter: QPainter, rect: QRectF, text: object,
              role: Role = Role.TEXT, *, size: int = 11,
              align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
              bold: bool = False) -> None:
        font_role = FontRole.CHROME_MONO_BOLD if bold \
            else FontRole.CHROME_MONO
        painter.setFont(self._font(font_role, size))
        painter.setPen(self.colour(role))
        painter.drawText(rect, int(align), str(text))

    def _background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), self.colour(Role.SURFACE_PANEL))

    def _identity(self, painter: QPainter, *, icon_at_top: bool = True) -> None:
        eyebrow, title = self.identity_lines
        narrow_icon = icon_at_top and self.DESIGN_WIDTH < 350
        identity_width = self.DESIGN_WIDTH - (58 if narrow_icon else 20)
        self._text(
            painter, QRectF(10, 5, identity_width, 16), eyebrow,
            Role.TEXT_DIM, size=10, align=Qt.AlignmentFlag.AlignCenter,
            elide=True,
        )
        right_margin = 48 if narrow_icon else 12
        self._text(
            painter, QRectF(8, 21, self.DESIGN_WIDTH - right_margin, 28),
            title, Role.TEXT, size=16, bold=True,
            align=Qt.AlignmentFlag.AlignCenter, elide=True,
        )
        if icon_at_top:
            self._return_icon(painter)

    def _return_icon(self, painter: QPainter) -> None:
        if self.RETURN_RECT.isEmpty() or not self._available_actions:
            return
        draw_faceplate_icon(
            painter, self.RETURN_RECT, "faceplate_return", button=True,
            pressed=self._pressed == "faceplate_return", enabled=True,
        )

    def _button(self, painter: QPainter, rect: QRectF, text: str,
                *, enabled: bool = True) -> None:
        painter.setPen(QPen(self.colour(Role.LINE), 1.0))
        painter.setBrush(self.colour(
            Role.SURFACE_FIELD if enabled else Role.SURFACE_PANEL_ALT))
        painter.drawRect(rect)
        self._text(
            painter, rect, text,
            Role.TEXT if enabled else Role.TEXT_DIM,
            size=12, bold=True, align=Qt.AlignmentFlag.AlignCenter,
        )

    def _hit_name(self, point: QPointF) -> str:
        if self._available_actions and self.RETURN_RECT.contains(point):
            return "faceplate_return"
        return ""

    def _activate(self, name: str) -> None:
        if name == "faceplate_return":
            self.action_requested.emit(self._return_intent)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hit = self._hit_name(event.position())
        if hit != self._hover:
            self._hover = hit
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if hit
                else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = ""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._hit_name(event.position())
            self.update()
            if self._pressed:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            released = self._hit_name(event.position())
            pressed = self._pressed
            self._pressed = ""
            self.update()
            if pressed and released == pressed:
                self._activate(pressed)
                event.accept()
                return
        super().mouseReleaseEvent(event)


class XmtrFaceplateSurface(MeasuredFaceplateSurface):
    """Xmtr_fp: four inputs, output, selection algorithm and operator pick."""

    DESIGN_WIDTH = 250
    DESIGN_HEIGHT = 412
    RETURN_RECT = QRectF(211, 374, 34, 34)
    INPUT_ROWS = tuple(QRectF(13, 78 + index * 27, 224, 24)
                       for index in range(4))
    OPERATOR_ROWS = tuple(QRectF(20, 278 + index * 20, 184, 19)
                          for index in range(5))

    @classmethod
    def geometry_contract(cls) -> dict:
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "input_rows": cls.INPUT_ROWS,
            "operator_rows": cls.OPERATOR_ROWS,
            "return": cls.RETURN_RECT,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter, icon_at_top=False)
        self._text(painter, QRectF(12, 55, 100, 20), "Disable", size=11,
                   bold=True)
        for index, row in enumerate(self.INPUT_ROWS, start=1):
            writable = self._write_allowed(f"in{index}.disabled")
            disabled = _truth(self._bound, f"in{index}.disabled")
            mark = "☑" if disabled else "☐"
            self._text(painter, QRectF(row.x(), row.y(), 20, row.height()),
                       mark, Role.TEXT_DIM if writable else Role.TEXT_FAINT,
                       size=12,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._text(painter, QRectF(row.x() + 25, row.y(), 78, row.height()),
                       f"Input {index}", Role.TEXT_DIM, size=11)
            self._mono(
                painter, QRectF(row.x() + 102, row.y(), 106, row.height()),
                _value_text(self._bound, f"in{index}.value", decimals=2),
                Role.TEXT, size=11,
            )

        self._text(painter, QRectF(12, 187, 74, 25), "Output", size=12,
                   bold=True)
        self._mono(
            painter, QRectF(77, 187, 128, 25),
            _value_text(self._bound, "out.value", decimals=2),
            Role.TEXT, size=12, bold=True,
        )
        self._text(painter, QRectF(12, 226, 156, 20), "Selection Type",
                   size=11, bold=True)
        selection = _value_text(
            self._bound, "select_type", decimals=0, missing=NO_VALUE)
        self._text(painter, QRectF(27, 246, 192, 24), selection,
                   Role.TEXT, size=11, elide=True)
        self._text(painter, QRectF(218, 246, 18, 24), "▾", Role.TEXT,
                   size=11, align=Qt.AlignmentFlag.AlignCenter)

        selected = _raw(self._bound, "operator_selection")
        if selected is None:
            selected = _raw(self._bound, "selected")
        labels = ("Use Selection Type", "Use Input 1", "Use Input 2",
                  "Use Input 3", "Use Input 4")
        selection_writable = self._write_allowed("operator_selection")
        for index, (row, label) in enumerate(zip(self.OPERATOR_ROWS, labels)):
            picked = str(selected).upper() in (
                str(index), f"IN{index}", f"IN_{index}", label.upper())
            self._text(painter, QRectF(row.x(), row.y(), 18, row.height()),
                       "◉" if picked else "○", Role.TEXT_DIM, size=11,
                       align=Qt.AlignmentFlag.AlignCenter)
            self._text(painter, QRectF(row.x() + 20, row.y(), 160, row.height()),
                       label, Role.TEXT if selection_writable
                       else Role.TEXT_FAINT, size=10)
        self._return_icon(painter)
        painter.end()

    def _hit_name(self, point: QPointF) -> str:
        common = super()._hit_name(point)
        if common:
            return common
        for index, row in enumerate(self.INPUT_ROWS, start=1):
            if QRectF(row.x(), row.y(), 25, row.height()).contains(point) \
                    and _available(self._bound, f"in{index}.disabled") \
                    and self._write_allowed(f"in{index}.disabled"):
                return f"disable:{index}"
        for index, row in enumerate(self.OPERATOR_ROWS):
            if row.contains(point) \
                    and _available(self._bound, "operator_selection") \
                    and self._write_allowed("operator_selection"):
                return f"select:{index}"
        return ""

    def _activate(self, name: str) -> None:
        if name.startswith("disable:"):
            index = int(name.partition(":")[2])
            if not self._write_allowed(f"in{index}.disabled"):
                return
            current = _truth(self._bound, f"in{index}.disabled")
            self.write_requested.emit(f"in{index}.disabled", not current)
            return
        if name.startswith("select:"):
            if not self._write_allowed("operator_selection"):
                return
            self.write_requested.emit(
                "operator_selection", int(name.partition(":")[2]))
            return
        super()._activate(name)


class ThreeInputSelectFaceplateSurface(MeasuredFaceplateSurface):
    """Truthful Xmtr-family adaptation for MIN_SELECT and MAX_SELECT.

    These blocks select algorithmically from three inputs.  They expose no
    disable flags, selection-type setting, or operator override, so the
    surface presents only their real input/output/winner contract instead of
    drawing convincing but inert Xmtr controls.
    """

    DESIGN_WIDTH = 250
    DESIGN_HEIGHT = 412
    RETURN_RECT = QRectF(211, 374, 34, 34)
    INPUT_ROWS = tuple(QRectF(14, 92 + index * 48, 222, 38)
                       for index in range(3))

    @classmethod
    def geometry_contract(cls) -> dict:
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "input_rows": cls.INPUT_ROWS,
            "return": cls.RETURN_RECT,
        }

    def input_value_text(self, index: int) -> str:
        """Operator text for a real input; useful to visual regression tests."""
        return _selector_value_text(
            self._bound, f"in{int(index)}.value", decimals=2)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter, icon_at_top=False)
        self._text(
            painter, QRectF(14, 62, 222, 22), "Selector Inputs",
            Role.TEXT, size=11, bold=True,
            align=Qt.AlignmentFlag.AlignCenter,
        )
        selected = _raw(self._bound, "selected")
        try:
            selected_index = int(selected)
        except (TypeError, ValueError):
            selected_index = 0
        for index, row in enumerate(self.INPUT_ROWS, start=1):
            active = index == selected_index
            painter.setPen(QPen(self.colour(Role.LINE), 1.0))
            painter.setBrush(self.colour(
                Role.SURFACE_FIELD if active else Role.SURFACE_PANEL_ALT))
            painter.drawRect(row)
            self._text(
                painter, QRectF(row.x() + 8, row.y(), 78, row.height()),
                f"Input {index}", Role.ACTION if active else Role.TEXT_DIM,
                size=11, bold=active,
            )
            self._mono(
                painter, QRectF(row.x() + 88, row.y(), 124, row.height()),
                self.input_value_text(index),
                Role.ACTION if active else Role.TEXT, size=11,
                bold=active,
            )

        self._text(painter, QRectF(14, 252, 80, 26), "Output", size=12,
                   bold=True)
        self._mono(
            painter, QRectF(94, 252, 142, 26),
            _value_text(self._bound, "out.value", decimals=2),
            Role.TEXT, size=12, bold=True,
        )
        winner = f"Input {selected_index}" if 1 <= selected_index <= 3 \
            else NO_VALUE
        self._text(painter, QRectF(14, 300, 92, 22), "Selected Input",
                   Role.TEXT_DIM, size=11)
        self._text(painter, QRectF(108, 300, 128, 22), winner,
                   Role.ACTION if selected_index else Role.TEXT_DIM,
                   size=11, bold=bool(selected_index))
        self._return_icon(painter)
        painter.end()


class ECTLSLFaceplateSurface(MeasuredFaceplateSurface):
    """ECTLSL_fp anatomy over the trainer's narrower CTLSL contract.

    The reference reserves sixteen stable row positions.  CTLSL currently has
    three real selector inputs, so rows four through sixteen display an absent
    mark rather than a fabricated name or value.
    """

    DESIGN_WIDTH = 250
    DESIGN_HEIGHT = 412
    RETURN_RECT = QRectF(213, 5, 32, 32)
    ROW_COUNT = 16
    TABLE_RECT = QRectF(12, 190, 226, 210)
    ROW_HEIGHT = 12.0

    @classmethod
    def geometry_contract(cls) -> dict:
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "rows": cls.ROW_COUNT,
            "table": cls.TABLE_RECT,
            "return": cls.RETURN_RECT,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter)
        self._text(painter, QRectF(34, 57, 92, 17), "Selection Type:",
                   Role.TEXT_DIM, size=10)
        self._text(painter, QRectF(130, 57, 94, 17),
                   _value_text(self._bound, "select_type", decimals=0),
                   Role.TEXT, size=10, elide=True)
        self._text(painter, QRectF(34, 76, 92, 17), "Selection Input:",
                   Role.TEXT_DIM, size=10)
        self._text(painter, QRectF(130, 76, 94, 17),
                   _value_text(self._bound, "selected", decimals=0),
                   Role.TEXT, size=10, elide=True)
        self._text(painter, QRectF(12, 98, 226, 18), "Operator Selection",
                   Role.TEXT_DIM, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        self._text(painter, QRectF(12, 116, 226, 18),
                   _value_text(self._bound, "operator_selection", decimals=0),
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter, elide=True)

        painter.fillRect(QRectF(14, 140, 12, 12), self.colour(Role.ACTION))
        self._text(painter, QRectF(14, 139, 12, 13), "!", Role.SURFACE_FIELD,
                   size=10, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        self._text(painter, QRectF(31, 135, 92, 23),
                   _value_text(self._bound, "mode", decimals=0),
                   Role.TEXT, size=14)
        self._mono(painter, QRectF(128, 132, 107, 25),
                   _value_text(self._bound, "out.value", decimals=2),
                   Role.TEXT, size=14, bold=True)
        self._text(painter, QRectF(140, 157, 90, 18),
                   _units(self._bound, "out.value"),
                   Role.TEXT_DIM, size=9,
                   align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._text(painter, QRectF(12, 178, 28, 14), "Input", Role.TEXT_DIM,
                   size=9)
        self._text(painter, QRectF(42, 178, 118, 14),
                   "Available Selections", Role.TEXT_DIM, size=9)
        self._text(painter, QRectF(164, 178, 72, 14), "Value", Role.TEXT_DIM,
                   size=9)
        selected = str(_raw(self._bound, "selected", "")).upper()
        for row_index in range(self.ROW_COUNT):
            index = row_index + 1
            y = self.TABLE_RECT.y() + row_index * self.ROW_HEIGHT
            present = _available(self._bound, f"in{index}.value")
            picked = selected in (str(index), f"IN{index}", f"IN_{index}")
            role = Role.ACTION if picked else Role.TEXT if present else Role.TEXT_FAINT
            self._mono(painter, QRectF(12, y, 28, self.ROW_HEIGHT), index,
                       role, size=9,
                       align=Qt.AlignmentFlag.AlignCenter)
            description = _value_text(
                self._bound, f"in{index}.description", decimals=0,
                missing="—" if not present else f"Input {index}")
            self._text(painter, QRectF(42, y, 118, self.ROW_HEIGHT),
                       description, role, size=9, elide=True)
            self._mono(painter, QRectF(163, y, 73, self.ROW_HEIGHT),
                       _value_text(self._bound, f"in{index}.value", decimals=2,
                                   missing="—"),
                       role, size=9)
        painter.end()

    def _hit_name(self, point: QPointF) -> str:
        common = super()._hit_name(point)
        if common:
            return common
        if self.TABLE_RECT.contains(point):
            index = int((point.y() - self.TABLE_RECT.y()) // self.ROW_HEIGHT) + 1
            if 1 <= index <= self.ROW_COUNT and _available(
                    self._bound, f"in{index}.value") and _available(
                        self._bound, "operator_selection") and \
                    self._write_allowed("operator_selection"):
                return f"select:{index}"
        return ""

    def _activate(self, name: str) -> None:
        if name.startswith("select:"):
            if not self._write_allowed("operator_selection"):
                return
            self.write_requested.emit(
                "operator_selection", int(name.partition(":")[2]))
            return
        super()._activate(name)


class VoterFaceplateSurface(MeasuredFaceplateSurface):
    """Shared measured AVTR_fp / DVTR_fp tabbed operational summary."""

    DESIGN_WIDTH = 440
    DESIGN_HEIGHT = 372
    RETURN_RECT = QRectF(368, 29, 34, 34)
    PANE_RECT = QRectF(33, 122, 374, 221)
    ANALOG = True
    ANALOG_TABS = ("Trip", "Pre-Trip", "Bypass", "Startup", "Alert", "Misc")
    DISCRETE_TABS = ("Trip", "Bypass", "Startup", "Alert")

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(palette, parent)
        self._selected_tab = 0

    @property
    def tab_names(self) -> tuple[str, ...]:
        return self.ANALOG_TABS if self.ANALOG else self.DISCRETE_TABS

    @property
    def tab_rects(self) -> tuple[QRectF, ...]:
        width = 50.0
        return tuple(QRectF(33 + index * width, 101, width, 21)
                     for index in range(len(self.tab_names)))

    @classmethod
    def geometry_contract(cls) -> dict:
        names = cls.ANALOG_TABS if cls.ANALOG else cls.DISCRETE_TABS
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "tabs": names,
            "pane": cls.PANE_RECT,
            "return": cls.RETURN_RECT,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter)
        for index, (name, rect) in enumerate(zip(self.tab_names, self.tab_rects)):
            painter.setPen(QPen(self.colour(Role.LINE), 1.0))
            painter.setBrush(self.colour(
                Role.SURFACE_FIELD if index == self._selected_tab
                else Role.SURFACE_PANEL_ALT))
            painter.drawRect(rect)
            self._text(painter, rect, name,
                       Role.TEXT if index == self._selected_tab else Role.TEXT_DIM,
                       size=9, align=Qt.AlignmentFlag.AlignCenter)
        plus = QRectF(33 + len(self.tab_names) * 50, 101, 25, 21)
        self._text(painter, plus, "+", Role.TEXT, size=9,
                   align=Qt.AlignmentFlag.AlignCenter)
        painter.setPen(QPen(self.colour(Role.LINE), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.PANE_RECT)
        method = getattr(self, f"_paint_{self.tab_names[self._selected_tab].lower().replace('-', '_')}")
        method(painter)
        painter.end()

    def _rows(self, painter: QPainter, rows: Iterable[tuple[str, str, str]],
              *, y: float = 145, spacing: float = 27) -> None:
        for index, (label, value, unit) in enumerate(rows):
            top = y + index * spacing
            self._text(painter, QRectF(95, top, 173, 20), label,
                       Role.TEXT_DIM, size=11)
            self._mono(painter, QRectF(270, top, 98, 20), value,
                       Role.TEXT, size=11)
            if unit:
                self._text(painter, QRectF(371, top, 31, 20), unit,
                           Role.TEXT_DIM, size=10, elide=True)

    def _paint_trip(self, painter: QPainter) -> None:
        rows = []
        if self.ANALOG:
            detect = _value_text(self._bound, "detect_type", decimals=0)
            limit = _value_text(self._bound, "trip_limit", decimals=2)
            unit = _units(self._bound, "in1.value")
            rows.append((f"Trip Limit    {detect}", limit, unit))
        rows.extend((
            ("Current votes to trip:",
             _value_text(self._bound, "trip_votes", decimals=0), ""),
            ("Votes required to trip:",
             _value_text(self._bound, "votes_required", decimals=0), ""),
            ("Trip status:",
             _value_text(self._bound, "trip_status", decimals=0), ""),
            ("Trip delay time:",
             _value_text(self._bound, "trip_delay", decimals=1), "s"),
            ("Normal delay time:",
             _value_text(self._bound, "normal_delay", decimals=1), "s"),
        ))
        if self.ANALOG:
            rows.append(("Trip hysteresis:",
                         _value_text(self._bound, "trip_hysteresis", decimals=1),
                         "%"))
        y = 142 if self.ANALOG else 151
        spacing = 27 if self.ANALOG else 26
        self._rows(painter, rows, y=y, spacing=spacing)
        self._mono(painter, QRectF(30, 218, 60, 20),
                   _value_text(self._bound, "delay_timer", decimals=1),
                   Role.TEXT, size=9)

    def _paint_pre_trip(self, painter: QPainter) -> None:
        rows = (
            ("Pre-trip limit:",
             _value_text(self._bound, "pre_trip_limit", decimals=2),
             _units(self._bound, "in1.value")),
            ("Current votes to pre-trip:",
             _value_text(self._bound, "pre_votes", decimals=0), ""),
            ("Votes required to pre-trip:",
             _value_text(self._bound, "votes_required", decimals=0), ""),
            ("Pre-trip status:",
             _value_text(self._bound, "pre_status", decimals=0), ""),
            ("Trip delay time:",
             _value_text(self._bound, "trip_delay", decimals=1), "s"),
            ("Normal delay time:",
             _value_text(self._bound, "normal_delay", decimals=1), "s"),
            ("Trip hysteresis:",
             _value_text(self._bound, "trip_hysteresis", decimals=1), "%"),
        )
        self._rows(painter, rows, y=142, spacing=27)
        self._mono(painter, QRectF(30, 218, 60, 20),
                   _value_text(self._bound, "pre_delay_timer", decimals=1),
                   Role.TEXT, size=9)

    def _paint_bypass(self, painter: QPainter) -> None:
        rows = (
            ("Allowed bypass time:",
             _value_text(self._bound, "bypass.allowed_time", decimals=1), "s"),
            ("Bypass timer:",
             _value_text(self._bound, "bypass_timer", decimals=1), "s"),
            ("Reminder time:",
             _value_text(self._bound, "bypass.reminder_time", decimals=1), "s"),
        )
        self._rows(painter, rows, y=142, spacing=29)
        hours = _value_text(self._bound, "bypass_timer_h", decimals=1,
                            missing="")
        if hours:
            self._mono(painter, QRectF(326, 171, 58, 20), hours,
                       Role.TEXT, size=10)
            self._text(painter, QRectF(386, 171, 16, 20), "hr",
                       Role.TEXT_DIM, size=9)
        message = ""
        if _truth(self._bound, "alarm.bypassed_pre_trip"):
            message = "A bypassed input exceeds the pre-trip limit"
        elif _truth(self._bound, "alarm.bypassed_trip"):
            message = "A bypassed input is in the trip state"
        elif _truth(self._bound, "alarm.bypass"):
            message = "An input is bypassed"
        self._text(painter, QRectF(58, 245, 322, 21), message,
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter, elide=True)
        enabled = _available(self._bound, "bypass.allow") \
            and self._write_allowed("bypass.allow")
        self._button(painter, QRectF(121, 268, 135, 28), "Allow Bypass",
                     enabled=enabled)

    def _paint_startup(self, painter: QPainter) -> None:
        rows = (
            ("Startup time:",
             _value_text(self._bound, "startup.time", decimals=1), "s"),
            ("Startup timer:",
             _value_text(self._bound, "startup.timer", decimals=1), "s"),
            ("Stabilization time:",
             _value_text(self._bound, "startup.stable_time", decimals=1), "s"),
            ("Time to stable (last):",
             _value_text(self._bound, "startup.time_to_stable", decimals=1), "s"),
            ("Stabilization timer:",
             _value_text(self._bound, "startup.stable", decimals=1), "s"),
            ("Reminder time:",
             _value_text(self._bound, "bypass.reminder_time", decimals=1), "s"),
        )
        self._rows(painter, rows, y=137, spacing=25)
        messages = []
        if _truth(self._bound, "startup.active"):
            messages.append("Trip inhibited - startup active")
        if _truth(self._bound, "startup.waiting_reset"):
            messages.append("Waiting for startup reset event")
        self._text(painter, QRectF(66, 292, 305, 36), "\n".join(messages),
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

    def _paint_alert(self, painter: QPainter) -> None:
        alert_specs = [
            ("alarm.trip", "Tripped"),
            ("alarm.pre_trip", "Pre-Tripped"),
            ("alarm.bypass", "An input is bypassed"),
            ("alarm.startup", "Startup is active"),
            ("alarm.deviation", "High/Low deviation limit exceeded"),
            ("alarm.expiration", "Reminder is active"),
            ("alarm.input_bad", "An input has Bad status"),
            ("alarm.bypassed_pre_trip",
             "A bypassed input exceeds the pre-trip limit"),
            ("alarm.bypassed_trip", "A bypassed input exceeds the trip limit"),
        ]
        if not self.ANALOG:
            alert_specs = [spec for spec in alert_specs
                           if spec[0] not in (
                               "alarm.pre_trip", "alarm.deviation",
                               "alarm.bypassed_pre_trip")]
        active = [label for key, label in alert_specs if _truth(self._bound, key)]
        if not active:
            self._text(painter, QRectF(55, 155, 330, 24), "No active alerts",
                       Role.TEXT_DIM, size=11,
                       align=Qt.AlignmentFlag.AlignCenter)
            return
        for index, label in enumerate(active):
            self._text(painter, QRectF(55, 150 + index * 18, 330, 18), label,
                       Role.ALARM_P1_TEXT, size=10,
                       align=Qt.AlignmentFlag.AlignCenter, elide=True)

    def _paint_misc(self, painter: QPainter) -> None:
        rows = (
            ("Deviation Limit:",
             _value_text(self._bound, "deviation.limit", decimals=1),
             _units(self._bound, "in1.value")),
            ("Deviation Hysteresis:",
             _value_text(self._bound, "deviation.hysteresis", decimals=1), "%"),
        )
        self._rows(painter, rows, y=190, spacing=31)

    def _hit_name(self, point: QPointF) -> str:
        common = super()._hit_name(point)
        if common:
            return common
        for index, rect in enumerate(self.tab_rects):
            if rect.contains(point):
                return f"tab:{index}"
        if self.tab_names[self._selected_tab] == "Bypass" \
                and QRectF(121, 268, 135, 28).contains(point) \
                and _available(self._bound, "bypass.allow") \
                and self._write_allowed("bypass.allow"):
            return "allow_bypass"
        return ""

    def _activate(self, name: str) -> None:
        if name.startswith("tab:"):
            self._selected_tab = int(name.partition(":")[2])
            self.update()
            return
        if name == "allow_bypass":
            if not self._write_allowed("bypass.allow"):
                return
            self.write_requested.emit("bypass.allow", True)
            return
        super()._activate(name)


class AVTRFaceplateSurface(VoterFaceplateSurface):
    ANALOG = True


class DVTRFaceplateSurface(VoterFaceplateSurface):
    ANALOG = False


class SEQFaceplateSurface(MeasuredFaceplateSurface):
    """SEQ_fp: current state over the fixed sixteen-output scan list."""

    DESIGN_WIDTH = 367
    DESIGN_HEIGHT = 468
    RETURN_RECT = QRectF(319, 19, 34, 34)
    ROW_COUNT = 16
    ROW_TOP = 128
    ROW_HEIGHT = 20

    @classmethod
    def geometry_contract(cls) -> dict:
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "rows": cls.ROW_COUNT,
            "return": cls.RETURN_RECT,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter)
        self._text(painter, QRectF(10, 59, 347, 19), "Current State :",
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        state_text = self.current_state_text()
        self._text(painter, QRectF(20, 79, 327, 23),
                   state_text,
                   Role.ACTION, size=12,
                   align=Qt.AlignmentFlag.AlignCenter, elide=True)
        self._text(painter, QRectF(10, 106, 347, 18), "Outputs :",
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        authored_rows = []
        row_count = _bounded_count(
            self._bound, "num_rows", self.ROW_COUNT, self.ROW_COUNT)
        for index in range(1, row_count + 1):
            label = _raw(self._bound, f"row{index}")
            if label not in (None, ""):
                authored_rows.append((
                    str(label), _truth(self._bound, f"row{index}.state")))
        if not authored_rows:
            authored_rows = self._sfc_chart_rows()
        for row_index, (label, active) in enumerate(
                authored_rows[:self.ROW_COUNT]):
            self._text(
                painter,
                QRectF(33, self.ROW_TOP + row_index * self.ROW_HEIGHT,
                       301, self.ROW_HEIGHT),
                label, Role.ACTION if active else Role.TEXT_DIM,
                size=10, elide=True,
            )
        painter.end()

    def current_state_text(self) -> str:
        """Resolve SEQ state or the SFC adapter's active step without blanks."""
        state_text = _value_text(self._bound, "state.name", decimals=0)
        if state_text not in (NO_VALUE, ""):
            return state_text
        active = _value_text(
            self._bound, "step.active", decimals=0, missing="")
        if active:
            return active
        return _value_text(self._bound, "step.number", decimals=0)

    def _sfc_chart_rows(self) -> list[tuple[str, bool]]:
        """Adapt SFC_CHART's real chart document to the SEQ_fp scan list."""
        chart_result = result_of(self._bound, "chart")
        if chart_result is UNRESOLVED or chart_result.value is None:
            return []
        try:
            chart = json.loads(chart_result.value) \
                if isinstance(chart_result.value, str) else chart_result.value
        except (TypeError, ValueError):
            return []
        if not isinstance(chart, dict):
            return []
        active = str(_raw(self._bound, "step.active", ""))
        rows = []
        for step in chart.get("steps", []):
            if not isinstance(step, dict):
                continue
            identity = str(step.get("id", ""))
            label = str(step.get("name") or identity)
            if label:
                rows.append((label, active in (identity, label)))
        return rows


class STDFaceplateSurface(MeasuredFaceplateSurface):
    """STD_fp: current state and two stable sixteen-row transition columns."""

    DESIGN_WIDTH = 620
    DESIGN_HEIGHT = 400
    RETURN_RECT = QRectF(545, 13, 34, 34)
    ROW_COUNT = 16
    ROW_TOP = 124
    ROW_HEIGHT = 15

    @classmethod
    def geometry_contract(cls) -> dict:
        return {
            "design": (cls.DESIGN_WIDTH, cls.DESIGN_HEIGHT),
            "rows": cls.ROW_COUNT,
            "return": cls.RETURN_RECT,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._background(painter)
        self._identity(painter)
        self._text(painter, QRectF(10, 58, 600, 18), "Current State :",
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        self._text(painter, QRectF(100, 77, 420, 21),
                   _value_text(self._bound, "state.name", decimals=0),
                   Role.ACTION, size=12,
                   align=Qt.AlignmentFlag.AlignCenter, elide=True)
        self._text(painter, QRectF(8, 101, 300, 18), "Transition Condition :",
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        self._text(painter, QRectF(312, 101, 300, 18), "Transition State :",
                   Role.TEXT, size=10,
                   align=Qt.AlignmentFlag.AlignCenter)
        matrix_destinations = self.transition_destinations()
        row_count = _bounded_count(
            self._bound, "num_rows", self.ROW_COUNT, self.ROW_COUNT)
        for row_index in range(row_count):
            index = row_index + 1
            condition = _raw(self._bound, f"row{index}")
            destination = _raw(self._bound, f"row{index}.destination")
            if destination in (None, ""):
                destination = matrix_destinations.get(index)
            active = _truth(self._bound, f"row{index}.state")
            y = self.ROW_TOP + row_index * self.ROW_HEIGHT
            if condition not in (None, ""):
                self._text(painter, QRectF(13, y, 294, self.ROW_HEIGHT),
                           condition,
                           Role.ACTION if active else Role.TEXT_FAINT,
                           size=9, elide=True)
            if destination not in (None, ""):
                self._text(painter, QRectF(312, y, 295, self.ROW_HEIGHT),
                           destination, Role.ACTION, size=9, elide=True)
        painter.end()

    def transition_destinations(self) -> dict[int, str]:
        """Return current-state destinations from STD's addressable MATRIX.

        MATRIX is the block's real configured transition table.  Parsing it
        here is preferable to inventing ``rowN.destination`` parameters that
        the block does not publish.  Malformed, absent, and zero cells remain
        absent on the faceplate.
        """
        raw_matrix = _raw(self._bound, "matrix")
        try:
            state = int(_raw(self._bound, "state.name"))
        except (TypeError, ValueError):
            return {}
        try:
            matrix = json.loads(raw_matrix) \
                if isinstance(raw_matrix, str) else raw_matrix
        except (TypeError, ValueError):
            return {}
        destinations: dict[int, int] = {}
        try:
            if isinstance(matrix, dict):
                row = matrix.get(str(state), matrix.get(state))
                if not isinstance(row, (list, tuple)):
                    return {}
                for index, destination in enumerate(row[:self.ROW_COUNT], 1):
                    destinations[index] = int(destination)
            elif isinstance(matrix, list):
                for entry in matrix:
                    if not isinstance(entry, dict) \
                            or int(entry.get("state", -1)) != state:
                        continue
                    target = entry.get("next", 0)
                    if isinstance(target, (list, tuple)):
                        for index, destination in enumerate(
                                target[:self.ROW_COUNT], 1):
                            destinations[index] = int(destination)
                    else:
                        transition = int(entry.get("trans", 0))
                        if 1 <= transition <= self.ROW_COUNT:
                            destinations[transition] = int(target)
        except (TypeError, ValueError):
            return {}
        return {
            index: str(destination)
            for index, destination in destinations.items()
            if 1 <= destination <= self.ROW_COUNT
        }


# ``render.PvmFaceplateWidget`` consumes this map.  It is kept separate from
# render.py so measured families can grow without turning the window host into
# another faceplate catalogue.
FACEPLATE_SURFACES = {
    "ISELFaceplate": XmtrFaceplateSurface,
    "LowSelectFaceplate": ThreeInputSelectFaceplateSurface,
    "HighSelectFaceplate": ThreeInputSelectFaceplateSurface,
    "CTLSLFaceplate": ECTLSLFaceplateSurface,
    "AVTRFaceplate": AVTRFaceplateSurface,
    "DVTRFaceplate": DVTRFaceplateSurface,
    "SEQFaceplate": SEQFaceplateSurface,
    "SfcChartFaceplate": SEQFaceplateSurface,
    "STDFaceplate": STDFaceplateSurface,
}

# Explicit name used by the render host to distinguish these measured plates
# from sectional visuals.  Keep the older descriptive map as an alias for
# catalogue callers and tests.
WHOLE_SURFACES = FACEPLATE_SURFACES


__all__ = [
    "AVTRFaceplateSurface",
    "DVTRFaceplateSurface",
    "ECTLSLFaceplateSurface",
    "FACEPLATE_SURFACES",
    "MeasuredFaceplateSurface",
    "SEQFaceplateSurface",
    "STDFaceplateSurface",
    "ThreeInputSelectFaceplateSurface",
    "WHOLE_SURFACES",
    "XmtrFaceplateSurface",
]
