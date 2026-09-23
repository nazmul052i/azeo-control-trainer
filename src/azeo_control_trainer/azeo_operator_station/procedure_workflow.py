"""Read-only retained workflow for active and nested advisory procedures."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
from azeo_control_trainer.core.presentation.dialog_layout import guarded_action
from azeo_control_trainer.core.procedures.flow import layout_flow, linear_flow


class ProcedureWorkflow(QWidget):
    def __init__(self, station, session):
        super().__init__()
        self.station, self.session = station, session
        self.definition = None
        self._states = None
        self.nodes, self.edges = {}, {}
        layout = QVBoxLayout(self)
        commands = FlowLayout()
        self.summary = QLabel("Select a procedure")
        self.summary.setWordWrap(True)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find step, equipment or call path")
        self.find.setClearButtonEnabled(True)
        self.find.returnPressed.connect(guarded_action(self.locate_text, self.summary))
        commands.addWidget(self.find)
        for label, callback in (("Find", self.locate_text), ("Locate response / active step", self.locate_current), ("Fit workflow", self.fit)):
            button = QPushButton(label)
            button.clicked.connect(guarded_action(callback, self.summary))
            commands.addWidget(button)
        layout.addLayout(commands)
        layout.addWidget(self.summary)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setAccessibleName("Live procedure workflow and call hierarchy")
        layout.addWidget(self.view, 1)

    def load(self, definition):
        if self.definition is definition:
            return
        self.definition = definition
        self.scene.clear()
        self.nodes, self.edges = {}, {}
        self._states = None
        if definition is None:
            return
        proc = definition.procedure
        self.graph = proc.flow.model_dump(mode="json") if proc.flow else linear_flow([s.model_dump(mode="json") for s in proc.steps])
        positions = layout_flow(self.graph)
        self.steps = {s.id: s for s in proc.steps}
        for node in self.graph["nodes"]:
            x, y = positions[node["id"]]
            rect = self.scene.addRect(QRectF(x, y, 330, 108))
            rect.setZValue(1)
            text = self.scene.addText("", QFont("Segoe UI", 9))
            text.setTextWidth(306)
            text.setPos(x + 10, y + 6)
            text.setZValue(2)
            text.setAcceptedMouseButtons(Qt.NoButton)
            self.nodes[node["id"]] = (node, rect, text)
        for index, edge in enumerate(self.graph["edges"]):
            a, b = positions[edge["source"]], positions[edge["target"]]
            start, end = QPointF(a[0] + 165, a[1] + 108), QPointF(b[0] + 165, b[1])
            path = QPainterPath(start)
            if end.y() <= start.y():
                side = min(a[0], b[0]) - 25 - index % 6 * 10
                path.lineTo(start.x(), start.y() + 18)
                path.lineTo(side, start.y() + 18)
                path.lineTo(side, end.y() - 18)
                path.lineTo(end.x(), end.y() - 18)
            else:
                middle = (start.y() + end.y()) / 2
                path.lineTo(start.x(), middle)
                path.lineTo(end.x(), middle)
            path.lineTo(end)
            path.moveTo(end + QPointF(-4, -7))
            path.lineTo(end)
            path.lineTo(end + QPointF(4, -7))
            line = self.scene.addPath(path)
            label = edge.get("label") or edge.get("condition") or ("Default" if edge.get("is_default") else edge.get("outcome", "always"))
            line.setToolTip(f"{edge['source']} → {edge['target']}\n{label}")
            self.edges[(edge["source"], edge["target"])] = line
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30))
        self.update_state()
        self.fit()

    def update_state(self):
        if self.definition is None:
            return
        active = self.definition is self.session.definition
        states = self.session.step_states if active else {}
        palette = self.station.palette_roles
        key = (tuple(states.items()), self.session.generation if active else 0, id(palette))
        if self._states == key:
            return
        self._states = key
        self.view.setBackgroundBrush(QColor(palette[Role.SURFACE_BG]))
        for identity, (node, rect, text) in self.nodes.items():
            step = self.steps.get(node.get("step_id"))
            status = states.get(step.id, "PENDING") if step else self.session.flow_states.get(identity, "PENDING") if active else "PENDING"
            color = palette[Role.TEXT] if status == "ACTIVE" else palette[Role.LINE]
            rect.setPen(QPen(QColor(color), 2.5 if status == "ACTIVE" else 1))
            rect.setBrush(QColor(palette[Role.SURFACE_FIELD]))
            label = step.description if step else node.get("label") or node["kind"].replace("_", " ").title()
            text.setPlainText(f"{step.id if step else identity}\n{status} · {step.type if step else node['kind']}\n{label[:130]}")
            text.setDefaultTextColor(QColor(palette[Role.TEXT]))
            rect.setToolTip(f"{identity}\n{label}\n{status}")
        selected = self.session.flow_path if active else None
        for pair, line in self.edges.items():
            line.setPen(QPen(QColor(palette[Role.ACTION] if pair == selected else palette[Role.LINE_SOFT]), 2.5 if pair == selected else 1))
        pending = self.session.prompt["step"]["id"] if active and self.session.prompt else "None"
        active_steps = [key for key, state in states.items() if state == "ACTIVE"]
        self.summary.setText(f"{self.definition.procedure.name} · Active: {', '.join(active_steps) or 'None'} · Response: {pending}")

    def locate(self, identity):
        for key, (node, rect, _) in self.nodes.items():
            if identity in {key, node.get("step_id")}:
                self.view.resetTransform()
                self.view.ensureVisible(rect, 60, 60)
                return True
        return False

    def locate_current(self):
        prompt = self.session.prompt
        identity = prompt["step"]["id"] if prompt else next((key for key, status in self.session.step_states.items() if status == "ACTIVE"), "")
        self.locate(identity)

    def locate_text(self):
        query = self.find.text().strip().casefold()
        if not query:
            return
        for key, (_, _, text) in self.nodes.items():
            if query in text.toPlainText().casefold():
                self.locate(key)
                return
        self.summary.setText("No matching step or call path")

    def fit(self):
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
