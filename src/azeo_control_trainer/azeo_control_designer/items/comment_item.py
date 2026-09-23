"""Comment/annotation item -- yellow sticky-note style, ISA-101 Silver theme.

Users can add text comments on the strategy canvas for documentation.
Double-click to edit text inline. Resizable via corner drag handle.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsObject, QGraphicsSceneMouseEvent,
    QStyle,
)


# Sizing
GRID_SIZE = 10
COMMENT_MIN_W = 80
COMMENT_MIN_H = 40
COMMENT_CORNER_RADIUS = 3
_HANDLE_SIZE = 10

# Colors -- soft yellow sticky-note
_BG_COLOR = QColor("#FFFDE7")
_BORDER_COLOR = QColor("#E0D9A8")
_BORDER_SELECTED = QColor("#3574C4")
_TEXT_COLOR = QColor("#5D4037")
_HEADER_COLOR = QColor("#FFF9C4")
_GRIP_COLOR = QColor("#C8C08A")


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


class CommentItem(QGraphicsObject):
    """Yellow sticky-note style comment box on the strategy canvas.

    Features:
    - Movable and selectable
    - Resizable via bottom-right corner grip
    - Double-click to edit text inline
    - Serializable for save/load
    """

    positionChanged = Signal()

    def __init__(self, text: str = "Comment", x: float = 0, y: float = 0,
                 w: float = 150, h: float = 60, parent=None):
        super().__init__(parent)
        self._text = text
        self._width = max(w, COMMENT_MIN_W)
        self._height = max(h, COMMENT_MIN_H)

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)  # Below blocks (5), above wires (1)

        # Resize state
        self._resizing = False
        self._resize_origin = QPointF()
        self._resize_start_w = 0.0
        self._resize_start_h = 0.0

        self.setPos(_snap(x), _snap(y))

    # ---------------------------------------------------------------- properties

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str):
        self._text = value
        self.update()

    # ---------------------------------------------------------------- geometry

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, self._width + 4, self._height + 4)

    def _hit_grip(self, pos: QPointF) -> bool:
        """Check if position is in the resize grip area (bottom-right corner)."""
        return (pos.x() >= self._width - _HANDLE_SIZE and
                pos.y() >= self._height - _HANDLE_SIZE)

    # ---------------------------------------------------------------- paint

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self._width, self._height)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # Background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_BG_COLOR))
        painter.drawRoundedRect(rect, COMMENT_CORNER_RADIUS, COMMENT_CORNER_RADIUS)

        # Subtle header strip (top 6px)
        header = QRectF(0, 0, self._width, 6)
        painter.setBrush(QBrush(_HEADER_COLOR))
        painter.drawRoundedRect(
            header.adjusted(0, 0, 0, COMMENT_CORNER_RADIUS),
            COMMENT_CORNER_RADIUS, COMMENT_CORNER_RADIUS,
        )
        painter.drawRect(QRectF(0, 3, self._width, 3))

        # Border
        border_color = _BORDER_SELECTED if is_selected else _BORDER_COLOR
        pen_w = 1.5 if is_selected else 1
        painter.setPen(QPen(border_color, pen_w))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            rect.adjusted(pen_w / 2, pen_w / 2, -pen_w / 2, -pen_w / 2),
            COMMENT_CORNER_RADIUS, COMMENT_CORNER_RADIUS,
        )

        # Text
        painter.setPen(QPen(_TEXT_COLOR))
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        text_rect = QRectF(6, 10, self._width - 12, self._height - 16)
        painter.drawText(text_rect,
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         self._text)

        # Resize grip (bottom-right corner, shown when selected)
        if is_selected:
            painter.setPen(QPen(_GRIP_COLOR, 1))
            x0 = self._width - 4
            y0 = self._height - 4
            for i in range(3):
                offset = i * 3
                painter.drawPoint(QPointF(x0 - offset, y0))
                painter.drawPoint(QPointF(x0, y0 - offset))

    # ---------------------------------------------------------------- interaction

    def hoverMoveEvent(self, event):
        if self._hit_grip(event.pos()) and self.isSelected():
            self.setCursor(QCursor(Qt.SizeFDiagCursor))
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.LeftButton and self.isSelected():
            if self._hit_grip(event.pos()):
                self._resizing = True
                self._resize_origin = event.scenePos()
                self._resize_start_w = self._width
                self._resize_start_h = self._height
                self.setFlag(QGraphicsItem.ItemIsMovable, False)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            new_w = max(COMMENT_MIN_W, _snap(self._resize_start_w + delta.x()))
            new_h = max(COMMENT_MIN_H, _snap(self._resize_start_h + delta.y()))
            scene = self.scene()
            sheet = getattr(scene, "sheet_rect", None) if scene else None
            if sheet is not None:
                available_w = sheet.right() - self.pos().x() - 2
                available_h = sheet.bottom() - self.pos().y() - 2
                new_w = min(new_w, max(
                    COMMENT_MIN_W,
                    int(available_w // GRID_SIZE) * GRID_SIZE,
                ))
                new_h = min(new_h, max(
                    COMMENT_MIN_H,
                    int(available_h // GRID_SIZE) * GRID_SIZE,
                ))
            if new_w != self._width or new_h != self._height:
                self.prepareGeometryChange()
                self._width = new_w
                self._height = new_h
                self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if self._resizing:
            self._resizing = False
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Open inline text editor on double-click."""
        self._edit_text()
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            candidate = QPointF(_snap(value.x()), _snap(value.y()))
            scene = self.scene()
            constrain = getattr(scene, "constrain_item_position", None)
            if callable(constrain):
                candidate = constrain(self, candidate)
            return candidate
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.positionChanged.emit()
        return super().itemChange(change, value)

    def contextMenuEvent(self, event):
        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        menu = studio_menu("TEXT BOX")

        act_edit = menu.addAction("Edit Text...")
        act_edit.triggered.connect(self._edit_text)
        menu.addSeparator()

        act_del = menu.addAction("Delete Comment")
        act_del.triggered.connect(self._request_delete)

        menu.exec_transient(event.screenPos())

    def _edit_text(self):
        """Show dialog to edit comment text.

        Routed through the scene so the edit is undoable and marks the module
        modified; assigning ``self._text`` directly left the change off the
        undo stack and out of the dirty state.
        """
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            None, "Edit Comment", "Comment text:", self._text)
        if not ok:
            return
        scene = self.scene()
        setter = getattr(scene, "set_comment_text", None) if scene else None
        if callable(setter):
            setter(self, text)
        else:
            self._text = text
            self.update()

    def _request_delete(self):
        scene = self.scene()
        if scene and hasattr(scene, 'delete_comment'):
            scene.delete_comment(self)

    # ---------------------------------------------------------------- serialization

    def serialize(self) -> dict:
        """Serialize comment to dict for JSON save."""
        pos = self.pos()
        return {
            "type": "comment",
            "text": self._text,
            "x": pos.x(),
            "y": pos.y(),
            "w": self._width,
            "h": self._height,
        }

    @classmethod
    def deserialize(cls, d: dict) -> CommentItem:
        """Create CommentItem from saved dict."""
        return cls(
            text=d.get("text", "Comment"),
            x=d.get("x", 0),
            y=d.get("y", 0),
            w=d.get("w", 150),
            h=d.get("h", 60),
        )
