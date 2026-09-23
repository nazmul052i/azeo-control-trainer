"""Adapted original PA Designer canvas items; provenance in CANVAS_SOURCE.json.

The host owns gestures and persistence. These reusable items retain the original
port and elbow-routing geometry, with Azeo colors and weak canvas ownership.
"""
from __future__ import annotations

from typing import Any
import weakref
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsTextItem, QStyle
from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.function_block_style import (
    FUNCTION_BLOCK_BODY_BOTTOM,
    FUNCTION_BLOCK_BODY_TOP,
    FUNCTION_BLOCK_DEFAULT_HEADER,
    FUNCTION_BLOCK_NORMAL_BORDER,
    FUNCTION_BLOCK_SELECTED_BORDER,
    function_block_category_color,
)
from azeo_control_trainer.core.strategy.model.block_base import BlockCategory

_PROCEDURE_BLOCK_BODY = FUNCTION_BLOCK_BODY_BOTTOM
_PROCEDURE_BLOCK_HEADER = FUNCTION_BLOCK_DEFAULT_HEADER
_PROCEDURE_BLOCK_BORDER = FUNCTION_BLOCK_NORMAL_BORDER
_PROCEDURE_BLOCK_HEADER_HEIGHT = 30
_PROCEDURE_BLOCK_MUTED = UI.text_secondary
_PROCEDURE_BLOCK_SELECTION = FUNCTION_BLOCK_SELECTED_BORDER
PROCEDURE_STEP_CATEGORIES = {
    "instruction": BlockCategory.CONTROL,
    "operator_confirm": BlockCategory.CONTROL,
    "operator_input": BlockCategory.CONTROL,
    "operator_comment": BlockCategory.CONTROL,
    "check": BlockCategory.LOGIC,
    "permissive": BlockCategory.LOGIC,
    "watchdog": BlockCategory.SAFETY,
    "wait_until": BlockCategory.SFC,
    "delay": BlockCategory.SFC,
    "write_tag": BlockCategory.IO,
    "ramp_tag": BlockCategory.IO,
    "read_tag": BlockCategory.IO,
    "calculate": BlockCategory.MATH,
    "user_event": BlockCategory.SIGNAL,
    "subprocedure": BlockCategory.COMPOSITE,
    "warning": BlockCategory.SAFETY,
    "alarm": BlockCategory.SAFETY,
    "hold": BlockCategory.SAFETY,
    "abort": BlockCategory.SAFETY,
    "complete": BlockCategory.SFC,
}
_RUN_STATE_ACCENTS = {}


def procedure_block_header_color(step_type: str) -> str:
    """Use the same category color that Control Designer uses for its blocks."""
    category = PROCEDURE_STEP_CATEGORIES.get(str(step_type or ""))
    return function_block_category_color(category) if category else _PROCEDURE_BLOCK_HEADER

class FlowNodeGraphicsItem(QGraphicsRectItem):  # type: ignore[misc]
    """Procedure flow node using Control Designer's function-block grammar."""

    PORT_DIAMETER = 10.0

    def __init__(
        self,
        canvas: Any,
        node_id: str,
        kind: str,
        width: float,
        height: float,
        *,
        ports: bool = True,
        furniture: bool = True,
    ):
        super().__init__(0, 0, width, height)
        self.canvas = weakref.proxy(canvas)
        self.node_id = node_id
        self.kind = kind
        self.step_type = ""
        self.header_color = _PROCEDURE_BLOCK_HEADER
        self.accent_color = ""
        self.runtime_status = ""
        self.runtime_color: str | None = None
        self.setData(40, "procedure_function_block" if kind == "action" else "procedure_flow_symbol")
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(QColor(_PROCEDURE_BLOCK_BORDER), 1.2))
        self.setBrush(QBrush(QColor(_PROCEDURE_BLOCK_BODY)))

        self.header_band: Any = None
        if kind == "action" and furniture:
            header_path = QPainterPath()
            header_path.addRoundedRect(
                QRectF(0.7, 0.7, width - 1.4, _PROCEDURE_BLOCK_HEADER_HEIGHT),
                4,
                4,
            )
            self.header_band = QGraphicsPathItem(header_path, self)
            self.header_band.setPen(Qt.NoPen)
            self.header_band.setBrush(QBrush(QColor(self.header_color)))
            self.header_band.setAcceptedMouseButtons(Qt.NoButton)
            self.header_band.setData(40, "function_block_header")

        self.status_strip = self.status_text = None
        if furniture:
            self.status_strip = QGraphicsRectItem(1, height - 5, width - 2, 4, self)
            self.status_strip.setPen(Qt.NoPen)
            self.status_strip.setBrush(QBrush(QColor(UI.disabled)))
            self.status_strip.setAcceptedMouseButtons(Qt.NoButton)
            self.status_strip.setData(40, "function_block_state_strip")
            self.status_strip.setVisible(False)
            self.status_text = QGraphicsTextItem("", self)
            self.status_text.setFont(QFont("Segoe UI", 6, QFont.Bold))
            self.status_text.setDefaultTextColor(QColor(_PROCEDURE_BLOCK_MUTED))
            self.status_text.setAcceptedMouseButtons(Qt.NoButton)
            self.status_text.setZValue(2)
            self.status_text.setVisible(False)

        self.input_port = self._make_port("input", width / 2, 0) if ports and kind != "start" else None
        self.output_port = self._make_port("output", width / 2, height) if ports and kind != "end" else None

    def configure_block_visual(self, step_type: str) -> None:
        """Apply category identity without turning the whole card into state."""
        self.step_type = str(step_type or "")
        self.header_color = procedure_block_header_color(self.step_type)
        self.setData(41, self.header_color)
        if self.header_band is not None:
            self.header_band.setBrush(self._header_brush())
        self.update()

    def _body_brush(self) -> QBrush:
        gradient = QLinearGradient(0, self.rect().top(), 0, self.rect().bottom())
        gradient.setColorAt(0, QColor(FUNCTION_BLOCK_BODY_TOP))
        gradient.setColorAt(1, QColor(FUNCTION_BLOCK_BODY_BOTTOM))
        return QBrush(gradient)

    def _header_brush(self) -> QBrush:
        color = QColor(self.header_color)
        gradient = QLinearGradient(
            0,
            self.rect().top(),
            0,
            self.rect().top() + _PROCEDURE_BLOCK_HEADER_HEIGHT,
        )
        gradient.setColorAt(0, color.lighter(120))
        gradient.setColorAt(0.5, color)
        gradient.setColorAt(1, color.darker(110))
        return QBrush(gradient)

    def set_runtime_status(self, status: str | None) -> None:
        if self.status_strip is None:
            return
        self.runtime_status = str(status or "").upper()
        self.runtime_color = _RUN_STATE_ACCENTS.get(self.runtime_status)
        visible = self.runtime_color is not None
        self.status_strip.setVisible(visible)
        self.status_text.setVisible(visible)
        self.setData(43, self.runtime_status)
        if visible:
            color = QColor(self.runtime_color)
            self.status_strip.setBrush(QBrush(color))
            self.status_text.setDefaultTextColor(color)
            self.status_text.setPlainText(self.runtime_status)
            text_width = self.status_text.boundingRect().width()
            self.status_text.setPos(self.rect().right() - text_width - 7, self.rect().bottom() - 22)
        self.update()

    def paint(self, painter: Any, option: Any, widget: Any = None) -> None:
        """Keep flow grammar, while action nodes read as function blocks."""
        rect = self.rect()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isSelected():
            edge = _PROCEDURE_BLOCK_SELECTION
            width = 2.4
        elif self.runtime_color:
            edge = self.runtime_color
            width = 1.8
        elif option.state & QStyle.State_MouseOver:
            edge = UI.blue
            width = 1.6
        else:
            edge = _PROCEDURE_BLOCK_BORDER
            width = 1.2
        if self.kind in {"start", "end"}:
            painter.setPen(QPen(QColor(edge), width))
            painter.setBrush(self.brush())
            radius = rect.height() / 2
            painter.drawRoundedRect(rect, radius, radius)
        elif self.kind in {"choice", "loop"}:
            painter.setPen(QPen(QColor(edge), width))
            painter.setBrush(self.brush())
            inset = min(30.0, rect.width() * 0.14)
            shape = QPainterPath()
            shape.moveTo(rect.left() + inset, rect.top())
            shape.lineTo(rect.right() - inset, rect.top())
            shape.lineTo(rect.right(), rect.center().y())
            shape.lineTo(rect.right() - inset, rect.bottom())
            shape.lineTo(rect.left() + inset, rect.bottom())
            shape.lineTo(rect.left(), rect.center().y())
            shape.closeSubpath()
            painter.drawPath(shape)
        elif self.kind in {"parallel_fork", "parallel_join"}:
            bar = QRectF(rect.left(), rect.center().y() - 6, rect.width(), 12)
            painter.setPen(QPen(QColor(edge), width))
            painter.setBrush(QBrush(QColor(UI.text_secondary)))
            painter.drawRoundedRect(bar, 4, 4)
        elif self.kind == "transition":
            painter.setPen(QPen(QColor(edge), width))
            painter.setBrush(self.brush())
            painter.drawRoundedRect(rect, 3, 3)
            painter.drawLine(rect.left() + 8, rect.top(), rect.left() + 8, rect.bottom())
            painter.drawLine(rect.right() - 8, rect.top(), rect.right() - 8, rect.bottom())
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._body_brush())
            painter.drawRoundedRect(rect, 4, 4)
            if self.kind == "action":
                header = QRectF(
                    rect.left(),
                    rect.top(),
                    rect.width(),
                    _PROCEDURE_BLOCK_HEADER_HEIGHT,
                )
                painter.setBrush(self._header_brush())
                painter.drawRoundedRect(header.adjusted(0, 0, 0, 4), 4, 4)
                painter.drawRect(QRectF(
                    header.left(),
                    header.bottom() - 4,
                    header.width(),
                    4,
                ))
            if self.kind == "action" and self.accent_color:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(self.accent_color)))
                painter.drawRect(QRectF(
                    rect.left() + 1,
                    rect.top() + _PROCEDURE_BLOCK_HEADER_HEIGHT,
                    5,
                    rect.height() - _PROCEDURE_BLOCK_HEADER_HEIGHT - 2,
                ))
            if self.kind == "action":
                painter.setPen(QPen(QColor(self.header_color).darker(130), 1))
                painter.drawLine(
                    rect.left() + 1,
                    rect.top() + _PROCEDURE_BLOCK_HEADER_HEIGHT,
                    rect.right() - 1,
                    rect.top() + _PROCEDURE_BLOCK_HEADER_HEIGHT,
                )
            painter.setPen(QPen(QColor(edge), width))
            painter.setBrush(Qt.NoBrush)
            inset = width / 2
            painter.drawRoundedRect(
                rect.adjusted(inset, inset, -inset, -inset), 4, 4
            )

    def _make_port(self, direction: str, center_x: float, center_y: float) -> Any:
        radius = self.PORT_DIAMETER / 2
        port = QGraphicsEllipseItem(center_x - radius, center_y - radius, self.PORT_DIAMETER, self.PORT_DIAMETER, self)
        port.setPen(QPen(QColor(UI.blue), 1.4))
        port.setBrush(QBrush(QColor(_PROCEDURE_BLOCK_BODY)))
        port.setZValue(3)
        port.setData(10, direction)
        port.setData(11, self.node_id)
        port.setToolTip(f"{direction.title()} port: {self.node_id}")
        port.setAcceptedMouseButtons(Qt.NoButton)
        return port

    def port_scene_position(self, direction: str) -> Any:
        port = self.output_port if direction == "output" else self.input_port
        if port is None:
            return None
        return port.mapToScene(port.rect().center())

    def itemChange(self, change: Any, value: Any) -> Any:
        if change == QGraphicsRectItem.ItemPositionChange:
            value = QPointF(value)
            if getattr(self.canvas, "snap_to_grid", False):
                grid = 12.0
                value.setX(round(value.x() / grid) * grid)
                value.setY(round(value.y() / grid) * grid)
            return self.canvas.constrain_item_position(self, value)
        result = super().itemChange(change, value)
        if change == QGraphicsRectItem.ItemPositionHasChanged:
            self.canvas.update_edges_for_node(self.node_id)
            self.canvas._node_position_changed = True
            self.canvas._request_scene_extent_refresh()
        return result

    def mouseDoubleClickEvent(self, event: Any) -> None:
        step_id = str(self.data(0) or "")
        if event.button() == Qt.LeftButton and step_id:
            self.canvas.step_command_requested.emit("open", step_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FlowRouteHandle(QGraphicsRectItem):  # type: ignore[misc]
    SIZE = 12.0

    def __init__(self, edge: Any, index: int, position: Any):
        radius = self.SIZE / 2
        super().__init__(-radius, -radius, self.SIZE, self.SIZE, edge)
        self.edge = weakref.proxy(edge)
        self.index = index
        self._initializing = True
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsRectItem.ItemIgnoresTransformations, True)
        self.setCursor(Qt.SizeAllCursor)
        self.setPen(QPen(QColor(UI.blue), 2))
        self.setBrush(QBrush(QColor(UI.pane)))
        self.setZValue(8)
        self.setToolTip("Drag to reroute this connection")
        self.setPos(position)
        self._initializing = False

    def itemChange(self, change: Any, value: Any) -> Any:
        if change == QGraphicsRectItem.ItemPositionChange:
            value = QPointF(value)
            if getattr(self.edge.canvas, "snap_to_grid", False):
                grid = 12.0
                value.setX(round(value.x() / grid) * grid)
                value.setY(round(value.y() / grid) * grid)
            return self.edge.canvas.constrain_route_position(self.edge, value)
        result = super().itemChange(change, value)
        if change == QGraphicsRectItem.ItemPositionHasChanged and not self._initializing:
            self.edge._route_handle_moved(self.index, self.pos())
        return result

    def mousePressEvent(self, event: Any) -> None:
        self.edge.setSelected(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        super().mouseReleaseEvent(event)
        self.edge.commit_route()


class FlowEdgeGraphicsItem(QGraphicsPathItem):  # type: ignore[misc]
    def __init__(
        self,
        canvas: Any,
        source_item: FlowNodeGraphicsItem,
        target_item: FlowNodeGraphicsItem,
        color: str,
        label: str = "",
        waypoints: list[Any] | None = None,
        line_width: float = 2.5,
    ):
        super().__init__()
        self.canvas = weakref.proxy(canvas)
        self.source_item = source_item
        self.target_item = target_item
        self.source_id = source_item.node_id
        self.target_id = target_item.node_id
        self.color = QColor(color)
        self.line_width = float(line_width)
        self.custom_route = bool(waypoints)
        self.route_points = [QPointF(float(point.x), float(point.y)) for point in (waypoints or [])]
        self._route_handles: list[FlowRouteHandle] = []
        self._syncing_handles = False
        self._drag_handle_index: int | None = None
        self._drag_pointer_start: Any = None
        self._drag_point_start: Any = None
        self._drag_moved = False
        self.setPen(QPen(self.color, self.line_width))
        self.setFlag(QGraphicsPathItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(-1)
        self.setData(0, "__flow_edge__")
        self.setData(1, self.source_id)
        self.setData(2, self.target_id)
        self.setToolTip(
            f"Connection: {self.source_id} -> {self.target_id}\n"
            "Drag to reroute; double-click for connection properties"
        )
        self.label_item = QGraphicsTextItem(label[:42], self) if label else None
        if self.label_item:
            self.label_item.setDefaultTextColor(self.color)
            self.label_item.setAcceptedMouseButtons(Qt.NoButton)
        self.update_path()

    @staticmethod
    def path_between(start: Any, end: Any, waypoints: list[Any] | None = None) -> Any:
        """Draw orthogonal elbow routing with an arrowhead at the target."""
        path = QPainterPath(start)
        if waypoints:
            current = QPointF(start)
            for point in waypoints:
                route_point = QPointF(point)
                path.lineTo(current.x(), route_point.y())
                path.lineTo(route_point)
                current = route_point
            approach_y = end.y() - 18.0
            path.lineTo(current.x(), approach_y)
            path.lineTo(end.x(), approach_y)
            path.lineTo(end.x(), end.y())
        elif end.y() >= start.y() + 24.0:
            mid = (start.y() + end.y()) / 2
            path.lineTo(start.x(), mid)
            path.lineTo(end.x(), mid)
            path.lineTo(end.x(), end.y())
        else:
            side = max(start.x(), end.x()) + 110.0
            path.lineTo(start.x(), start.y() + 18.0)
            path.lineTo(side, start.y() + 18.0)
            path.lineTo(side, end.y() - 18.0)
            path.lineTo(end.x(), end.y() - 18.0)
            path.lineTo(end.x(), end.y())
        path.moveTo(end.x() - 5.0, end.y() - 8.0)
        path.lineTo(end.x(), end.y())
        path.lineTo(end.x() + 5.0, end.y() - 8.0)
        return path

    @staticmethod
    def _default_route_point(start: Any, end: Any) -> QPointF:
        if end.y() >= start.y() + 24.0:
            return QPointF(end.x(), (start.y() + end.y()) / 2)
        return QPointF(max(start.x(), end.x()) + 110.0, start.y() + 18.0)

    def _effective_route_points(self, start: Any, end: Any) -> list[QPointF]:
        if self.custom_route and self.route_points:
            return [self.canvas.constrain_scene_point(point) for point in self.route_points]
        return [self.canvas.constrain_scene_point(self._default_route_point(start, end))]

    def _sync_route_handles(self, points: list[Any]) -> None:
        # A hidden handle costs native construction and geometry notifications
        # for every wire. Materialize it only for the selected connection.
        if not self.isSelected():
            return
        while len(self._route_handles) < len(points):
            index = len(self._route_handles)
            self._route_handles.append(FlowRouteHandle(self, index, points[index]))
        while len(self._route_handles) > len(points):
            handle = self._route_handles.pop()
            scene = handle.scene()
            handle.setParentItem(None)
            if scene is not None:
                scene.removeItem(handle)
        self._syncing_handles = True
        try:
            for index, point in enumerate(points):
                self._route_handles[index].index = index
                self._route_handles[index].setPos(point)
                self._route_handles[index].setVisible(self.isSelected())
        finally:
            self._syncing_handles = False

    def _route_handle_moved(self, index: int, position: Any) -> None:
        if self._syncing_handles:
            return
        if not self.custom_route:
            start = self.source_item.port_scene_position("output")
            end = self.target_item.port_scene_position("input")
            self.route_points = self._effective_route_points(start, end)
            self.custom_route = True
        while len(self.route_points) <= index:
            self.route_points.append(QPointF(position))
        self.route_points[index] = QPointF(position)
        self.update_path()
        self.canvas._request_scene_extent_refresh()

    def commit_route(self) -> None:
        if not self.custom_route:
            return
        points = [
            {"x": round(point.x(), 1), "y": round(point.y(), 1)}
            for point in self.route_points
        ]
        self.canvas.connection_route_changed.emit(self.source_id, self.target_id, points)

    def reset_route(self) -> None:
        self.custom_route = False
        self.route_points = []
        self.update_path()

    def shape(self) -> Any:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(12.0, self.line_width + 8.0))
        return stroker.createStroke(self.path())

    def itemChange(self, change: Any, value: Any) -> Any:
        result = super().itemChange(change, value)
        if change == QGraphicsPathItem.ItemSelectedHasChanged:
            if value:
                self.update_path()
            for handle in self._route_handles:
                handle.setVisible(bool(value))
        return result

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.setSelected(True)
            start = self.source_item.port_scene_position("output")
            end = self.target_item.port_scene_position("input")
            points = self._effective_route_points(start, end)
            click = event.pos()
            self._drag_handle_index = min(
                range(len(points)),
                key=lambda index: (points[index].x() - click.x()) ** 2 + (points[index].y() - click.y()) ** 2,
            )
            self._drag_pointer_start = QPointF(click)
            self._drag_point_start = QPointF(points[self._drag_handle_index])
            self._drag_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_handle_index is not None and self._drag_pointer_start is not None:
            delta = event.pos() - self._drag_pointer_start
            position = self._drag_point_start + delta
            if getattr(self.canvas, "snap_to_grid", False):
                grid = 12.0
                position.setX(round(position.x() / grid) * grid)
                position.setY(round(position.y() / grid) * grid)
            position = self.canvas.constrain_scene_point(position)
            self._route_handle_moved(self._drag_handle_index, position)
            self._drag_moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._drag_handle_index is not None:
            moved = self._drag_moved
            self._drag_handle_index = None
            self._drag_pointer_start = None
            self._drag_point_start = None
            self._drag_moved = False
            if moved:
                self.commit_route()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.canvas.connection_edit_requested.emit(self.source_id, self.target_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def update_path(self) -> None:
        start = self.source_item.port_scene_position("output")
        end = self.target_item.port_scene_position("input")
        if start is None or end is None:
            return
        points = self._effective_route_points(start, end)
        self.setPath(self.path_between(start, end, points))
        self._sync_route_handles(points)
        if self.label_item:
            midpoint = self.path().pointAtPercent(0.52)
            self.label_item.setPos(midpoint.x() + 7, midpoint.y() - 11)
