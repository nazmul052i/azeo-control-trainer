"""DCS-style application tile for the Simulator Manager.

Each tile represents a launchable application (Operator Graphics,
Control Designer, Trend Viewer, etc.) with a painted icon, label,
and active-window indicator.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QBrush, QPainterPath
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QSizePolicy

from .hmi_theme import Colors


class ApplicationTile(QFrame):
    """Clickable tile with a painted icon and label."""

    clicked = Signal(str)  # emits the tile_id

    def __init__(self, tile_id: str, label: str, icon_type: str = "generic",
                 parent=None):
        super().__init__(parent)
        self._tile_id = tile_id
        self._label_text = label
        self._icon_type = icon_type
        self._is_active = False
        self._hovered = False

        self.setFixedSize(150, 120)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(2)

        # Icon area (painted in paintEvent)
        layout.addStretch()

        # Label
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(lbl)

    @property
    def tile_id(self) -> str:
        return self._tile_id

    @property
    def is_active(self) -> bool:
        return self._is_active

    @is_active.setter
    def is_active(self, val: bool):
        self._is_active = val
        self._update_style()
        self.update()

    def _update_style(self):
        border = Colors.TEXT_LINK if self._hovered else (
            Colors.STATE_RUNNING if self._is_active else "#C0C7D0")
        border_w = 2 if (self._hovered or self._is_active) else 1
        bg = "#F0F1F5" if self._hovered else Colors.BG_SECONDARY
        self.setStyleSheet(
            f"ApplicationTile {{ background: {bg}; "
            f"border: {border_w}px solid {border}; border-radius: 6px; }}"
        )

    def enterEvent(self, ev):
        self._hovered = True
        self._update_style()

    def leaveEvent(self, ev):
        self._hovered = False
        self._update_style()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self._tile_id)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Draw icon in the upper portion
        icon_rect = QRectF(self.width()/2 - 24, 12, 48, 48)
        self._draw_icon(p, icon_rect)

        # Active indicator dot
        if self._is_active:
            p.setBrush(QColor(Colors.STATE_RUNNING))
            p.setPen(Qt.NoPen)
            p.drawEllipse(self.width() - 16, 6, 10, 10)

        p.end()

    def _draw_icon(self, p: QPainter, r: QRectF):
        """Draw a simple geometric icon based on icon_type."""
        color = QColor(Colors.EQUIP_OUTLINE)
        fill = QColor(Colors.EQUIP_FILL)
        pen = QPen(color, 2)
        p.setPen(pen)
        p.setBrush(fill)

        cx, cy = r.center().x(), r.center().y()
        s = min(r.width(), r.height()) * 0.4

        if self._icon_type == "operator":
            # P&ID symbol: vessel + pipes
            p.drawRect(QRectF(cx-s*0.6, cy-s, s*1.2, s*1.6))
            p.drawLine(int(cx-s*1.2), int(cy), int(cx-s*0.6), int(cy))
            p.drawLine(int(cx+s*0.6), int(cy), int(cx+s*1.2), int(cy))
            p.drawLine(int(cx), int(cy-s*1.4), int(cx), int(cy-s))

        elif self._icon_type == "control":
            # FBD: two connected blocks
            b1 = QRectF(cx-s*1.1, cy-s*0.5, s, s*0.8)
            b2 = QRectF(cx+s*0.1, cy-s*0.5, s, s*0.8)
            p.drawRect(b1)
            p.drawRect(b2)
            p.drawLine(int(b1.right()), int(cy), int(b2.left()), int(cy))
            # PID label
            f = QFont("Consolas", 7)
            p.setFont(f)
            p.drawText(b1, Qt.AlignCenter, "PID")
            p.drawText(b2, Qt.AlignCenter, "AO")

        elif self._icon_type == "trend":
            # Trend line
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(cx-s, cy+s*0.3)
            path.cubicTo(cx-s*0.3, cy-s, cx+s*0.3, cy+s*0.5, cx+s, cy-s*0.3)
            pen2 = QPen(QColor(Colors.PV_NORMAL), 2.5)
            p.setPen(pen2)
            p.drawPath(path)
            # Axes
            p.setPen(QPen(color, 1))
            p.drawLine(int(cx-s*1.1), int(cy+s*0.6), int(cx+s*1.1), int(cy+s*0.6))
            p.drawLine(int(cx-s*1.1), int(cy-s), int(cx-s*1.1), int(cy+s*0.6))

        elif self._icon_type == "alarm":
            # Bell shape
            p.setBrush(QColor(Colors.ALARM_MEDIUM))
            path = QPainterPath()
            path.moveTo(cx-s*0.8, cy+s*0.3)
            path.quadTo(cx-s*0.8, cy-s, cx, cy-s*1.1)
            path.quadTo(cx+s*0.8, cy-s, cx+s*0.8, cy+s*0.3)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(int(cx-s), int(cy+s*0.3), int(cx+s), int(cy+s*0.3))
            p.drawEllipse(QRectF(cx-s*0.15, cy+s*0.3, s*0.3, s*0.3))

        elif self._icon_type == "dashboard":
            # Gauge
            p.drawArc(QRectF(cx-s, cy-s*0.6, s*2, s*2), 30*16, 120*16)
            p.drawLine(int(cx), int(cy+s*0.4), int(cx+s*0.5), int(cy-s*0.2))
            # Small bars
            for i in range(3):
                x = cx - s + i * s
                h = s * (0.4 + i * 0.3)
                p.drawRect(QRectF(x, cy+s*0.6-h, s*0.6, h))

        elif self._icon_type == "config":
            # Gear
            p.setBrush(fill)
            teeth = 8
            import math
            for i in range(teeth):
                a = i * 2 * math.pi / teeth
                x1 = cx + s * 0.9 * math.cos(a)
                y1 = cy + s * 0.9 * math.sin(a)
                p.drawEllipse(QRectF(x1-s*0.2, y1-s*0.2, s*0.4, s*0.4))
            p.drawEllipse(QRectF(cx-s*0.5, cy-s*0.5, s, s))
            p.setBrush(QColor(Colors.BG_SECONDARY))
            p.drawEllipse(QRectF(cx-s*0.25, cy-s*0.25, s*0.5, s*0.5))

        elif self._icon_type == "tags":
            # Tag/label
            p.drawRoundedRect(QRectF(cx-s*0.9, cy-s*0.5, s*1.8, s*1.0), 4, 4)
            f = QFont("Consolas", 7)
            p.setFont(f)
            p.drawText(QRectF(cx-s*0.9, cy-s*0.5, s*1.8, s*1.0),
                       Qt.AlignCenter, "TAG")

        elif self._icon_type == "opcua":
            # Network nodes
            for dx, dy in [(-s*0.6, -s*0.4), (s*0.6, -s*0.4), (0, s*0.5)]:
                p.drawEllipse(QRectF(cx+dx-s*0.25, cy+dy-s*0.25, s*0.5, s*0.5))
            p.setPen(QPen(color, 1.5))
            p.drawLine(int(cx-s*0.6), int(cy-s*0.4), int(cx+s*0.6), int(cy-s*0.4))
            p.drawLine(int(cx-s*0.6), int(cy-s*0.4), int(cx), int(cy+s*0.5))
            p.drawLine(int(cx+s*0.6), int(cy-s*0.4), int(cx), int(cy+s*0.5))

        else:
            # Generic: simple square
            p.drawRect(QRectF(cx-s*0.7, cy-s*0.7, s*1.4, s*1.4))
