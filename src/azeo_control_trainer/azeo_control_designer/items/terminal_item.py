"""Pin/terminal graphics item — ISA-101 Silver style.

Input pins are hollow circles; output pins are filled circles.
Colored by data type (REAL=blue, BOOL=gold, INT=green, ENUM=purple).
Hover expands the pin for easy targeting.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPainter
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem,
)

from azeo_control_trainer.core.strategy.model.terminal import (
    Terminal, TerminalDirection, DataType, Quality,
)


# Pin sizing — Azeo Control Designer draws pins as small stubs; ours
# were twice that and read as the diagram's subject instead of its
# punctuation (user feedback, with screenshots). Hover still grows the
# target, so small does not mean hard to hit.
TERM_RADIUS = 3.5
TERM_HOVER_RADIUS = 6.0
TERM_HIGHLIGHT_RADIUS = 7.5

# ISA-101 Silver colors by data type
_TYPE_COLORS = {
    DataType.FLOAT: QColor("#2868A8"),   # blue — analog
    DataType.BOOL:  QColor("#C89820"),   # gold — discrete
    DataType.INT:   QColor("#28884C"),   # green — integer
    DataType.STRING: QColor("#7A6AAA"),  # purple — string
    DataType.ENUM:  QColor("#7A6AAA"),   # purple — enum
}

_QUALITY_COLORS = {
    Quality.UNCERTAIN: QColor("#D88700"),
    Quality.BAD: QColor("#C62828"),
}
_FORCED_COLOR = QColor("#9C27B0")


class TerminalItem(QGraphicsEllipseItem):
    """Small circle representing a block terminal (port).

    Input terminals are hollow; output terminals are filled.
    Users click and drag from one terminal to another to create wires.
    """

    def __init__(self, terminal: Terminal, parent_block: QGraphicsItem):
        super().__init__(
            -TERM_RADIUS, -TERM_RADIUS,
            TERM_RADIUS * 2, TERM_RADIUS * 2,
            parent_block,
        )
        self.terminal = terminal
        self.parent_block = parent_block

        self._is_highlighted = False
        self._highlight_compatible = True

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CrossCursor)
        self.setZValue(10)

        self._wiring_feedback = ""
        self._refresh_tooltip(include_runtime=False)

        self._update_appearance()

    # ---------------------------------------------------------------- colors

    def _type_color(self) -> QColor:
        return _TYPE_COLORS.get(self.terminal.data_type, QColor("#2868A8"))

    def _display_color(self) -> QColor:
        """Return the live semantic colour without losing datatype offline.

        A red block border says that *something* is bad.  Colouring the exact
        terminal identifies the source of the bad status; a forced input is
        purple so it cannot be mistaken for an algorithm fault.
        """
        if self.terminal.forced:
            return _FORCED_COLOR
        if getattr(self.parent_block, "_live_mode", False):
            return _QUALITY_COLORS.get(
                self.terminal.status, self._type_color())
        return self._type_color()

    def _update_appearance(self):
        color = self._display_color()
        self.setPen(QPen(color.darker(120), 1.5))

        if self.terminal.direction == TerminalDirection.INPUT and not self.terminal.connected:
            # Hollow input pin
            self.setBrush(QBrush(Qt.NoBrush))
        else:
            # Filled output or connected input
            self.setBrush(QBrush(color))

    def refresh(self):
        """Update appearance after connection state change."""
        self._update_appearance()
        self._refresh_tooltip(include_runtime=True)
        self.update()

    def _refresh_tooltip(self, *, include_runtime: bool):
        """Keep type and engineering metadata visible during every mode."""
        terminal = self.terminal
        tip = (f"{terminal.name} ({terminal.direction.value})"
               f"\nType: {terminal.data_type.value}")
        if terminal.units:
            tip += f" · Units: {terminal.units}"
        if terminal.eu_range is not None:
            tip += f"\nRange: {terminal.eu_range[0]:g} to {terminal.eu_range[1]:g}"
        if include_runtime:
            tip += (f"\nValue: {terminal.value}"
                    f"\nQuality: {terminal.status.name}"
                    f"\nLimit: {terminal.limit.value}")
        if terminal.description:
            tip += f"\n{terminal.description}"
        if terminal.is_bkcal:
            tip += "\n[BKCAL — feedback wire]"
        if terminal.forced:
            tip += f"\nFORCED = {terminal.forced_value}"
        if self._wiring_feedback:
            tip += f"\n\n{self._wiring_feedback}"
        self.setToolTip(tip)

    # ---------------------------------------------------------------- geometry

    def get_scene_center(self) -> QPointF:
        """Center position in scene coordinates (for wire routing)."""
        return self.mapToScene(QPointF(0, 0))

    # ---------------------------------------------------------------- highlight (for wiring feedback)

    def set_highlighted(self, highlighted: bool, compatible: bool = True):
        self._is_highlighted = highlighted
        self._highlight_compatible = compatible
        self.update()

    def set_wiring_feedback(self, message: str = ""):
        """Show a transient compatibility reason without losing pin details."""
        self._wiring_feedback = message
        self._refresh_tooltip(include_runtime=True)

    # ---------------------------------------------------------------- paint

    def paint(self, painter: QPainter, option, widget=None):
        # Draw highlight ring if active
        if self._is_highlighted:
            if self._highlight_compatible:
                hl_color = QColor("#a6e3a1")   # green — compatible
            else:
                hl_color = QColor("#f38ba8")   # red — incompatible
            painter.setPen(QPen(hl_color, 2))
            painter.setBrush(QBrush(hl_color.lighter(150)))
            r = TERM_HIGHLIGHT_RADIUS
            painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        # Draw normal pin
        super().paint(painter, option, widget)

    # ---------------------------------------------------------------- hover

    def hoverEnterEvent(self, event):
        size = TERM_HOVER_RADIUS * 2
        self.setRect(-TERM_HOVER_RADIUS, -TERM_HOVER_RADIUS, size, size)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        size = TERM_RADIUS * 2
        self.setRect(-TERM_RADIUS, -TERM_RADIUS, size, size)
        super().hoverLeaveEvent(event)
