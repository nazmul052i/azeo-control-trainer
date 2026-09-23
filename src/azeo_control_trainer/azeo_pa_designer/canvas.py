"""Retained procedure canvas: pointer motion changes geometry, never documents."""
from __future__ import annotations

from functools import wraps
from collections import OrderedDict
import logging
import math

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QDrag, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QStaticText
from PySide6.QtWidgets import (
    QAbstractItemView, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QTreeWidget,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.studio_icons import studio_icon
from azeo_control_trainer.core.procedures.library import block_for_step, block_for_token
from ._canvas_primitives import FlowEdgeGraphicsItem, FlowNodeGraphicsItem, FlowRouteHandle

BLOCK_MIME = "application/x-azeo-procedure-block"
START, END = "$start", "$end"


def contain_event(method):
    @wraps(method)
    def contained(self, *args):
        try:
            return method(self, *args)
        except Exception as error:
            canvas = self if isinstance(self, ProcedureCanvas) else self.canvas if hasattr(self, "canvas") else self.edge.canvas
            canvas.report_error(error)
            if args and hasattr(args[-1], "accept"):
                args[-1].accept()
            return args[-1] if method.__name__ == "itemChange" else None
    return contained


# Item callbacks cross the same native boundary as view callbacks. The imported
# wire handles must not let an exception escape before the view can catch it.
for _item_type in (FlowNodeGraphicsItem, FlowEdgeGraphicsItem, FlowRouteHandle):
    for _name in ("itemChange", "mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent", "mouseDoubleClickEvent", "paint"):
        if _name in _item_type.__dict__:
            setattr(_item_type, _name, contain_event(getattr(_item_type, _name)))


class BlockPalette(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def startDrag(self, _actions):  # noqa: N802
        try:
            self._start_drag()
        except Exception as error:
            self.window().canvas.report_error(error)

    def _start_drag(self):
        item = self.currentItem()
        token = item.data(0, Qt.UserRole) if item else None
        if not token:
            return
        block = block_for_token(token)
        mime = QMimeData()
        mime.setData(BLOCK_MIME, token.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(studio_icon(block.icon, 32).pixmap(32, 32))
        self._drag = drag
        try:
            drag.exec(Qt.CopyAction)
        finally:
            self._drag = None


class ProcedureNode(FlowNodeGraphicsItem):
    WIDTH, HEIGHT = 336, 108

    def __init__(self, canvas, key, step=None, index=0, flow_node=None):
        super().__init__(canvas, key, flow_node["kind"] if flow_node else "action" if step is not None else "start" if key == START else "end",
                         self.WIDTH if step is not None else 144, self.HEIGHT if step is not None else 36, furniture=False)
        self.setZValue(1)
        self.step_id = None
        self._text = []
        self._fingerprint = None
        self._icon = None
        if step is not None:
            self.set_step(step, index)
        else:
            self.set_flow_node(flow_node or {"kind": "start" if key == START else "end"})

    def set_flow_node(self, node):
        self._text = []
        self._prepare_text(node.get("label") or node["kind"].replace("_", " ").title(), 12, 9, 9, True, 122)
        self.setToolTip(f"{node.get('id', '')}\n{node['kind']}\n{node.get('condition', '')}\nRight-click for properties and help")
        self.update()

    def boundingRect(self):  # noqa: N802
        return self.rect().adjusted(-8, -8, 8, 8)

    def _prepare_text(self, value, x, y, size=9, bold=False, width=310):
        text, font = self.canvas.cached_text(value, size, bold, width)
        self._text.append((QPointF(x, y), text, font))

    def set_step(self, step, index):
        signature = (index, *(str(step.get(key) or "") for key in
                     ("id", "type", "library_block_id", "description", "condition", "tag",
                      "value", "delay_sec", "comment_prompt", "section")))
        if signature == self._fingerprint:
            return
        self._fingerprint = signature
        self.step_id = step.get("id", "")
        self._text = []
        try:
            block = block_for_step(step)
            label, mark = block.label, block.icon
        except StopIteration:
            label, mark = step.get("type", "Step"), "step_block"
        self.configure_block_visual(step.get("type", ""))
        if mark not in self.canvas._icons:
            self.canvas._icons[mark] = studio_icon(mark, 18)
        self._icon = self.canvas._icons[mark]
        self._prepare_text(f"{index + 1:02d}   {self.step_id}", 12, 7, 9, True)
        self._prepare_text(label, 36, 38, 9, True, 285)
        detail = step.get("condition") or step.get("comment_prompt") or step.get("description") or "Configure this step"
        if step.get("type") == "delay":
            detail = f"{step.get('delay_sec', '')} simulation seconds"
        elif step.get("tag"):
            detail = f"{step['tag']}" + (f" = {step.get('value')}" if step.get("type") == "write_tag" else "")
        self._prepare_text(detail, 12, 66, 9)
        self.setToolTip(f"{label} · {self.step_id}\n{detail}\n{step.get('description', '')}")
        self.update()

    @contain_event
    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.step_id is not None:
            self._icon.paint(painter, 12, 37, 18, 18)
        for index, (position, text, font) in enumerate(self._text):
            painter.setPen(QColor("#FFFFFF") if self.step_id is not None and index == 0 else QColor(UI.text))
            painter.setFont(font)
            painter.drawStaticText(position, text)


class ProcedureAnnotation(QGraphicsRectItem):
    """Movable, non-executable context rendered behind workflow blocks."""

    def __init__(self, canvas, data):
        super().__init__()
        self.canvas, self.annotation_id = canvas, data["id"]
        self.label = QGraphicsSimpleTextItem(self)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(-2)
        self.apply(data)

    def apply(self, data):
        self.kind = data.get("kind", "note")
        self.setRect(0, 0, data.get("width", 300), data.get("height", 120))
        color = QColor(data.get("color", "#607D8B"))
        fill = QColor(color)
        fill.setAlpha(24 if self.kind in {"rectangle", "swimlane"} else 48)
        self.setBrush(fill)
        pen = QPen(color, 2 if self.kind in {"phase", "swimlane"} else 1)
        if self.kind == "rectangle":
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        prefix = {"phase": "PHASE · ", "swimlane": "SWIMLANE · "}.get(self.kind, "")
        self.label.setText(prefix + data.get("text", ""))
        font = QFont(self.canvas.font())
        font.setPointSize(9)
        font.setBold(self.kind in {"phase", "swimlane"})
        self.label.setFont(font)
        self.label.setBrush(QColor(UI.text))
        self.label.setPos(10, 8)
        self.setToolTip("Engineering annotation · does not execute\nDouble-click to edit")

    @contain_event
    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionChange:
            return self.canvas.constrain_item_position(self, value)
        return super().itemChange(change, value)

    @contain_event
    def mouseReleaseEvent(self, event):  # noqa: N802
        super().mouseReleaseEvent(event)
        self.canvas.annotation_geometry_changed.emit(
            self.annotation_id,
            {"x": round(self.x()), "y": round(self.y()), "width": round(self.rect().width()), "height": round(self.rect().height())},
        )

    @contain_event
    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.canvas.annotation_edit_requested.emit(self.annotation_id)
        event.accept()


class ProcedureCanvas(QGraphicsView):
    context_requested = Signal(object, object, object)
    step_selected = Signal(str)
    flow_selected = Signal(str)
    layout_changed = Signal(object)
    connection_requested = Signal(str, str)
    connection_route_changed = Signal(str, str, object)
    connection_edit_requested = Signal(str, str)
    step_command_requested = Signal(str, str)
    block_drop_requested = Signal(str, object, object)
    ui_error = Signal(str)
    zoom_changed = Signal(float)
    annotation_geometry_changed = Signal(str, object)
    annotation_edit_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.setItemIndexMethod(QGraphicsScene.BspTreeIndex)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QColor(UI.chrome_alt))
        self.setAcceptDrops(True)
        self.setAccessibleName("Visual procedure workflow")
        self.setMinimumWidth(180)
        self.nodes, self.edges, self.adjacent = {}, {}, {}
        self.annotations = {}
        self.order = []
        self.flow_ids = {}
        self.flow = None
        self.snap_to_grid = True
        self._syncing = False
        self._node_position_changed = False
        self._error_pending = False
        self._gesture = None
        self._route_gesture = {}
        self._pan_at = None
        self._connect_from = None
        self._preview = None
        self._drop_edge = None
        self._extent_timer = QTimer(self)
        self._extent_timer.setSingleShot(True)
        self._extent_timer.setInterval(40)
        self._extent_timer.timeout.connect(self._extend_scene)
        self._dirty_nodes = set()
        self._last_selected = None
        self.scene.selectionChanged.connect(self._selection_changed)
        self.edge_updates = 0
        self._auto_fit_width = True
        self._fonts, self._icons = {}, {}
        self._text_cache = OrderedDict()

    def cached_text(self, value, size, bold, width):
        key = (str(value), size, bold, width)
        if key in self._text_cache:
            self._text_cache.move_to_end(key)
            return self._text_cache[key]
        font_key = (size, bold)
        if font_key not in self._fonts:
            font = QFont(self.font())
            font.setPointSize(size)
            font.setBold(bold)
            self._fonts[font_key] = (font, QFontMetrics(font))
        font, metrics = self._fonts[font_key]
        text = QStaticText(metrics.elidedText(str(value).replace("\n", " "), Qt.ElideRight, width))
        text.setTextFormat(Qt.PlainText)
        text.prepare(font=font)
        self._text_cache[key] = (text, font)
        if len(self._text_cache) > 2048:
            self._text_cache.popitem(last=False)
        return text, font

    def report_error(self, error):
        logging.getLogger(__name__).exception("Procedure canvas event failed", exc_info=error)
        if not self._error_pending:
            self._error_pending = True
            try:
                self.cancel_gesture()
            except Exception:
                # The failing wire may also fail while restoring a drag. Keep
                # cleanup inside this boundary; the document restores on idle.
                logging.getLogger(__name__).exception("Procedure gesture cleanup failed")
                self._gesture = None
                self._route_gesture.clear()
                self._connect_from = None
            finally:
                self.ui_error.emit(str(error))
                QTimer.singleShot(0, self._clear_error)

    def _clear_error(self):
        self._error_pending = False

    @staticmethod
    def key(step_id):
        return "step:" + step_id

    def sync(self, steps, metadata, flow=None):
        """Retain existing items; structural commands only touch changed edges."""
        self._syncing = True
        try:
            keys = [self.key(step.get("id", "")) for step in steps]
            if len(set(keys)) != len(keys):
                return  # Keep the last unambiguous view while the draft reports duplicate IDs.
            self.flow = flow
            step_map = {step["id"]: step for step in steps}
            flow_nodes = {}
            if flow:
                from azeo_control_trainer.core.procedures.flow import layout_flow
                flow_positions = layout_flow(flow)
                mapping = {n["id"]: self.key(n["step_id"]) if n.get("step_id") else "flow:" + n["id"] for n in flow["nodes"]}
                flow_nodes = {mapping[n["id"]]: n for n in flow["nodes"]}
                ordered = list(flow_nodes)
                pairs = [(mapping[e["source"]], mapping[e["target"]]) for e in flow["edges"]
                         if e.get("source") in mapping and e.get("target") in mapping]
                self.flow_ids = {v: k for k, v in mapping.items()}
                labels = {(mapping[e["source"]], mapping[e["target"]]): e for e in flow["edges"]
                          if e.get("source") in mapping and e.get("target") in mapping}
            else:
                ordered = [START, *keys, END]
                pairs = list(zip(ordered, ordered[1:]))
                self.flow_ids = {}
                labels = {}
            self.order = ordered
            wanted_edges = set(pairs)
            for pair in set(self.edges) - wanted_edges:
                edge = self.edges.pop(pair)
                self.scene.removeItem(edge)
                for key in pair:
                    self.adjacent.get(key, set()).discard(edge)
            for key in set(self.nodes) - set(ordered):
                self.scene.removeItem(self.nodes.pop(key))
                self.adjacent.pop(key, None)
            positions = {row["step_id"]: row for row in metadata.get("canvas_layout", [])}
            routes = {(row["source"], row["target"]): row["points"] for row in metadata.get("canvas_routes", [])}
            for index, key in enumerate(ordered):
                info = flow_nodes.get(key)
                step = step_map.get(info.get("step_id")) if info else steps[index - 1] if 0 < index < len(ordered) - 1 else None
                node = self.nodes.get(key)
                if node is None:
                    node = ProcedureNode(self, key, step, index - 1, info)
                    self.nodes[key] = node
                    self.adjacent[key] = set()
                    self.scene.addItem(node)
                if step is not None:
                    node.set_step(step, index - 1)
                elif info:
                    node.set_flow_node(info)
                saved = positions.get(step["id"] if step is not None else key)
                if saved:
                    node.setPos(float(saved["x"]), float(saved["y"]))
                else:
                    node.setPos(QPointF(*flow_positions[info["id"]]) + QPointF(96 if step is None else 0, 0)
                                if info else self.default_position(index, step is None))
            for pair in pairs:
                edge = self.edges.get(pair)
                created = edge is None
                if edge is None:
                    edge = FlowEdgeGraphicsItem(self, self.nodes[pair[0]], self.nodes[pair[1]], UI.field_hover, line_width=1.5)
                    edge.setToolTip("Execution order →\nDrop a block to insert here. Drag the wire to route it.\nDrag an output port to a block input to reorder the procedure.")
                    self.edges[pair] = edge
                    self.scene.addItem(edge)
                    for key in pair:
                        self.adjacent[key].add(edge)
                if pair in labels:
                    data = labels[pair]
                    edge.setToolTip(f"{data['source']} → {data['target']}\n{data.get('label', '')}\n"
                                    f"{data.get('condition') or ('Default exit' if data.get('is_default') else data.get('outcome', 'always'))}")
                points = [QPointF(row["x"], row["y"]) for row in routes.get(pair, [])]
                edge.route_points, edge.custom_route = points, bool(points)
                if points or not created:
                    edge.update_path()
            self._sync_annotations(metadata.get("annotations", []))
            self._bounds_to_items()
            self._node_position_changed = False
            self._dirty_nodes.clear()
        finally:
            self._syncing = False

    def update_step(self, step, index):
        node = self.nodes.get(self.key(step["id"]))
        if node:
            node.set_step(step, index)

    def _sync_annotations(self, rows):
        wanted = {row["id"]: row for row in rows}
        for identity in set(self.annotations) - set(wanted):
            self.scene.removeItem(self.annotations.pop(identity))
        for identity, row in wanted.items():
            item = self.annotations.get(identity)
            if item is None:
                item = ProcedureAnnotation(self, row)
                self.annotations[identity] = item
                self.scene.addItem(item)
            else:
                item.apply(row)
            item.setPos(row.get("x", 0), row.get("y", 0))

    def current_layout(self):
        return [{"step_id": node.step_id if node.step_id is not None else key,
                 "x": round(node.x()), "y": round(node.y())} for key, node in self.nodes.items()]

    def update_edges_for_node(self, key):
        self._dirty_nodes.add(key)
        if self._syncing:
            return
        for edge in self.adjacent.get(key, ()):
            edge.update_path()
            self.edge_updates += 1

    def _request_scene_extent_refresh(self):
        if not self._syncing and not self._extent_timer.isActive():
            self._extent_timer.start()

    @contain_event
    def _extend_scene(self):
        bounds = QRectF(self.sceneRect())
        for key in self._dirty_nodes:
            if key in self.nodes:
                bounds = bounds.united(self.nodes[key].sceneBoundingRect().adjusted(-100, -100, 180, 100))
        self._dirty_nodes.clear()
        self.setSceneRect(bounds)

    def _bounds_to_items(self):
        bounds = QRectF(0, 0, 700, 650)
        for node in self.nodes.values():
            bounds = bounds.united(node.sceneBoundingRect().adjusted(-100, -100, 180, 100))
        for annotation in self.annotations.values():
            bounds = bounds.united(annotation.sceneBoundingRect().adjusted(-40, -40, 40, 40))
        self.setSceneRect(bounds)

    @staticmethod
    def constrain_scene_point(point):
        return QPointF(min(1000000, max(-1000000, point.x())), min(1000000, max(-1000000, point.y())))

    def constrain_item_position(self, _item, position):
        return self.constrain_scene_point(position)

    def constrain_route_position(self, _edge, position):
        return self.constrain_scene_point(position)

    @contain_event
    def _selection_changed(self):
        if self._syncing:
            return
        chosen = next((item.step_id for item in self.scene.selectedItems()
                       if isinstance(item, ProcedureNode) and item.step_id is not None), None)
        if chosen != self._last_selected:
            self._last_selected = chosen
            self.step_selected.emit(chosen or "")

    def select_step(self, step_id, *, reveal=False):
        node = self.nodes.get(self.key(step_id))
        if not node:
            return
        self._syncing = True
        try:
            self.scene.clearSelection()
            node.setSelected(True)
            self._last_selected = step_id
            if reveal:
                self.ensureVisible(node, 40, 40)
        finally:
            self._syncing = False

    def select_steps(self, ids):
        self._syncing = True
        try:
            self.scene.clearSelection()
            for step_id in ids:
                node = self.nodes.get(self.key(step_id))
                if node:
                    node.setSelected(True)
        finally:
            self._syncing = False
        self._selection_changed()

    @contain_event
    def contextMenuEvent(self, event):  # noqa: N802
        point = event.pos()
        if event.reason() == QContextMenuEvent.Keyboard:
            selected = self.scene.selectedItems()
            if selected:
                point = self.mapFromScene(selected[0].sceneBoundingRect().center())
        target = ("canvas", None)
        for item in self.items(point):
            owner = item
            while owner.parentItem() is not None:
                owner = owner.parentItem()
            if isinstance(owner, ProcedureNode) and owner.step_id is not None:
                if not owner.isSelected():
                    self.select_step(owner.step_id)
                self.step_selected.emit(owner.step_id)
                target = ("block", owner.step_id)
                break
            if isinstance(owner, ProcedureNode) and self.flow:
                if not owner.isSelected():
                    self.scene.clearSelection()
                    owner.setSelected(True)
                target = ("flow", owner.node_id)
                break
            if isinstance(owner, FlowEdgeGraphicsItem):
                self.scene.clearSelection()
                owner.setSelected(True)
                target = ("wire", (owner.source_id, owner.target_id))
                break
            if isinstance(owner, ProcedureAnnotation):
                self.scene.clearSelection()
                owner.setSelected(True)
                target = ("annotation", owner.annotation_id)
                break
        self.context_requested.emit(target, self.viewport().mapToGlobal(point), self.mapToScene(point))
        event.accept()

    def arrange(self):
        if self.flow:
            from azeo_control_trainer.core.procedures.flow import layout_flow
            positions = layout_flow(self.flow)
            for key, node in self.nodes.items():
                node.setPos(QPointF(*positions[self.flow_ids[key]]) + QPointF(96 if node.step_id is None else 0, 0))
            self._bounds_to_items()
            self.commit_layout()
            return
        for index, key in enumerate(self.order):
            node = self.nodes[key]
            node.setPos(self.default_position(index, node.step_id is None))
        self._bounds_to_items()
        self.commit_layout()

    def align_selected(self):
        nodes = [item for item in self.scene.selectedItems() if isinstance(item, ProcedureNode)]
        if len(nodes) > 1:
            left = min(node.x() for node in nodes)
            for node in nodes:
                node.setX(left)
            self.commit_layout()

    def commit_layout(self):
        if self._node_position_changed:
            self._node_position_changed = False
            self.layout_changed.emit(self.current_layout())

    def zoom_fit(self):
        self._auto_fit_width = False
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def zoom_reset(self):
        self._auto_fit_width = False
        self.resetTransform()
        self.zoom_changed.emit(self.transform().m11())

    def zoom_by(self, factor):
        self._auto_fit_width = False
        if 0.08 <= self.transform().m11() * factor <= 2.5:
            self.scale(factor, factor)
            self.zoom_changed.emit(self.transform().m11())

    @staticmethod
    def default_position(index, endpoint):
        return QPointF(60 + (max(0, index - 1) // 32) * 492 + (96 if endpoint else 0),
                       24 if index == 0 else 96 + ((index - 1) % 32) * 144)

    def fit_width(self):
        if not self._auto_fit_width:
            return
        scale = min(1.0, max(0.25, (self.viewport().width() - 24) / 400))
        self.resetTransform()
        self.scale(scale, scale)
        self.zoom_changed.emit(self.transform().m11())
        node = self.nodes.get(self.key(self._last_selected or "")) or self.nodes.get(START)
        if node:
            center = node.sceneBoundingRect().center()
            self.centerOn(center.x(), max(center.y(), self.viewport().height() / scale / 2))

    @contain_event
    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.fit_width()

    @contain_event
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.fit_width()

    def _port_at(self, position, direction):
        for item in self.items(position):
            if item.data(10) == direction:
                return item.data(11)
        return None

    def _edge_at(self, position):
        return next((item for item in self.items(position) if isinstance(item, FlowEdgeGraphicsItem)), None)

    def _highlight_drop(self, edge):
        if edge is self._drop_edge:
            return
        if self._drop_edge:
            self._drop_edge.setPen(QPen(self._drop_edge.color, self._drop_edge.line_width))
        self._drop_edge = edge
        if edge:
            edge.setPen(QPen(QColor(UI.blue), 3))

    def cancel_gesture(self):
        if self._preview is not None:
            self.scene.removeItem(self._preview)
            self._preview = None
        self._connect_from = None
        self._pan_at = None
        self._highlight_drop(None)
        if self._gesture:
            self._syncing = True
            try:
                for key, point in self._gesture.items():
                    if key in self.nodes:
                        self.nodes[key].setPos(point)
            finally:
                self._syncing = False
            for key in self._gesture:
                self.update_edges_for_node(key)
        self._gesture = None
        for pair, (custom, points) in self._route_gesture.items():
            edge = self.edges.get(pair)
            if edge:
                edge.custom_route, edge.route_points = custom, points
                edge._drag_handle_index = None
                edge.update_path()
        self._route_gesture.clear()
        self._node_position_changed = False
        self.viewport().unsetCursor()

    @contain_event
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._auto_fit_width = False
            self._pan_at = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        port = self._port_at(event.position().toPoint(), "output")
        if event.button() == Qt.LeftButton and port:
            self._connect_from = port
            self._preview = self.scene.addPath(QPainterPath(), QPen(QColor(UI.blue), 2, Qt.DashLine))
            self._preview.setZValue(3)
            event.accept()
            return
        super().mousePressEvent(event)
        self._gesture = {key: QPointF(node.pos()) for key, node in self.nodes.items() if node.isSelected()}
        self._route_gesture = {pair: (edge.custom_route, [QPointF(point) for point in edge.route_points])
                               for pair, edge in self.edges.items() if edge.isSelected()}

    @contain_event
    def mouseMoveEvent(self, event):  # noqa: N802
        point = event.position().toPoint()
        if self._pan_at is not None:
            delta = point - self._pan_at
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_at = point
            event.accept()
        elif self._connect_from:
            self._preview.setPath(FlowEdgeGraphicsItem.path_between(
                self.nodes[self._connect_from].port_scene_position("output"), self.mapToScene(point)))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    @contain_event
    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._pan_at is not None:
            self._pan_at = None
            self.viewport().unsetCursor()
            event.accept()
            return
        if self._connect_from:
            source = self._connect_from
            target = self._port_at(event.position().toPoint(), "input")
            self.cancel_gesture()
            if target and source != target:
                self.connection_requested.emit(source, target)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._gesture = None
        self._route_gesture.clear()
        self.commit_layout()

    @contain_event
    def mouseDoubleClickEvent(self, event):  # noqa: N802
        super().mouseDoubleClickEvent(event)

    @contain_event
    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.cancel_gesture()
            event.accept()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            selected = [item.step_id for item in self.scene.selectedItems()
                        if isinstance(item, ProcedureNode) and item.step_id is not None]
            if selected:
                self.step_command_requested.emit("delete", "\n".join(selected))
            event.accept()
        else:
            super().keyPressEvent(event)

    @contain_event
    def wheelEvent(self, event):  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(math.pow(1.0015, event.angleDelta().y()))
            event.accept()
        else:
            super().wheelEvent(event)

    @contain_event
    def focusOutEvent(self, event):  # noqa: N802
        if self._connect_from or self._pan_at is not None:
            self.cancel_gesture()
        super().focusOutEvent(event)

    @contain_event
    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    @contain_event
    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasFormat(BLOCK_MIME):
            self._highlight_drop(self._edge_at(event.position().toPoint()))
            event.acceptProposedAction()
        else:
            event.ignore()

    @contain_event
    def dragLeaveEvent(self, event):  # noqa: N802
        self._highlight_drop(None)
        event.accept()

    @contain_event
    def dropEvent(self, event):  # noqa: N802
        if not event.mimeData().hasFormat(BLOCK_MIME):
            event.ignore()
            return
        token = bytes(event.mimeData().data(BLOCK_MIME)).decode("utf-8")
        block_for_token(token)
        point = event.position().toPoint()
        edge = self._edge_at(point)
        pair = (edge.source_id, edge.target_id) if edge else None
        self._highlight_drop(None)
        self.block_drop_requested.emit(token, self.mapToScene(point), pair)
        event.acceptProposedAction()
