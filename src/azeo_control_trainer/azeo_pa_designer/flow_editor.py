"""Explicit workflow connections; the canvas and worksheet edit one document."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.procedures.flow import linear_flow
from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
from .editing import guarded

FLOW_KINDS = {"choice": "Decision", "loop": "Retry loop", "transition": "Transition",
              "merge": "Merge", "parallel_fork": "Parallel split", "parallel_join": "Parallel join", "end": "End"}


class WorkflowEditor(QWidget):
    NODE_FIELDS = ("id", "kind", "label", "step_id", "condition", "completion_mode", "operator_prompt",
                   "timeout_sec", "poll_sec", "stable_for_sec", "max_iterations", "join_mode")
    EDGE_FIELDS = ("source", "target", "label", "condition", "outcome", "is_default", "priority")

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = FlowLayout()
        self.enable = QPushButton("Enable advanced workflow")
        self.enable.clicked.connect(self.enable_flow)
        bar.addWidget(self.enable)
        example = QPushButton("Open workflow example")
        example.clicked.connect(self.open_example)
        bar.addWidget(example)
        self.kind = QComboBox()
        for kind, label in FLOW_KINDS.items():
            self.kind.addItem(label, kind)
        bar.addWidget(self.kind)
        add = QPushButton("Add node")
        add.clicked.connect(lambda: self.add_node(self.kind.currentData()))
        bar.addWidget(add)
        for label, pattern in (("Decision pair", "decision"), ("Retry pair", "retry"), ("Parallel pair", "parallel")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, key=pattern: self.add_pattern(key))
            bar.addWidget(button)
        self.limit = QSpinBox()
        self.limit.setRange(1, 100000)
        self.limit.setSuffix(" maximum node visits")
        self.limit.setValue(10000)
        self.limit.valueChanged.connect(self.change_limit)
        bar.addWidget(self.limit)
        layout.addLayout(bar)
        note = QLabel("Connect ports on the Workflow canvas. Use a Decision for conditions, a Retry loop for bounded repetition, "
                      "and named success / failure / timeout paths. Validate before saving.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.nodes = self.table(self.NODE_FIELDS)
        self.edges = self.table(self.EDGE_FIELDS)
        # The worksheet remains the compatibility surface, while the typed
        # inspector owns specialist fields instead of forcing horizontal
        # scrolling through twelve mostly-empty columns.
        for column in range(4, len(self.NODE_FIELDS)):
            self.nodes.setColumnHidden(column, True)
        self.nodes.setColumnWidth(0, 150)
        self.nodes.setColumnWidth(1, 130)
        self.nodes.setColumnWidth(2, 230)
        self.nodes.setColumnWidth(3, 150)
        layout.addWidget(QLabel("Nodes — select a symbol on the canvas to locate its row"))
        layout.addWidget(self.nodes, 1)
        remove = QPushButton("Remove selected node")
        remove.clicked.connect(self.remove_node)
        layout.addWidget(remove, 0, Qt.AlignLeft)
        layout.addWidget(QLabel("Connections — conditions are evaluated in ascending priority; default is the fallback"))
        layout.addWidget(self.edges, 1)
        commands = FlowLayout()
        for label, callback in (("Add connection", self.add_edge), ("Remove selected connection", self.remove_edge)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            commands.addWidget(button)
        layout.addLayout(commands)
        self.property_tabs = QTabWidget()
        self.property_tabs.addTab(self._node_inspector(), "Selected node properties")
        self.property_tabs.addTab(self._edge_inspector(), "Selected connection properties")
        self.property_tabs.setMaximumHeight(250)
        self.property_tabs.setMinimumWidth(0)
        self.property_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.property_tabs)
        self.nodes.cellChanged.connect(lambda r, c: self.edit("nodes", r, c))
        self.edges.cellChanged.connect(lambda r, c: self.edit("edges", r, c))
        self.nodes.itemSelectionChanged.connect(self.load_node_inspector)
        self.edges.itemSelectionChanged.connect(self.load_edge_inspector)
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)

    def _node_inspector(self):
        panel = QWidget()
        form = QFormLayout(panel)
        self.node_identity = QLineEdit()
        self.node_identity.setReadOnly(True)
        self.node_kind = QLineEdit()
        self.node_kind.setReadOnly(True)
        self.node_label = QLineEdit()
        self.node_condition = QLineEdit()
        self.node_condition.setPlaceholderText("Process or memory expression")
        self.node_completion = QComboBox()
        self.node_completion.addItems(["process", "operator", "process_or_operator", "process_and_operator"])
        self.node_prompt = QLineEdit()
        self.node_timeout = QDoubleSpinBox()
        self.node_timeout.setRange(0, 86400)
        self.node_timeout.setSuffix(" s")
        self.node_poll = QDoubleSpinBox()
        self.node_poll.setDecimals(3)
        self.node_poll.setRange(.001, 3600)
        self.node_poll.setSuffix(" s")
        self.node_stable = QDoubleSpinBox()
        self.node_stable.setRange(0, 86400)
        self.node_stable.setSuffix(" s")
        self.node_iterations = QSpinBox()
        self.node_iterations.setRange(1, 10000)
        self.node_join = QComboBox()
        self.node_join.addItems(["and", "or"])
        for label, field in (("Identity", self.node_identity), ("Kind", self.node_kind), ("Label", self.node_label),
                             ("Process condition", self.node_condition), ("Completion", self.node_completion),
                             ("Operator prompt", self.node_prompt), ("Timeout", self.node_timeout),
                             ("Poll interval", self.node_poll), ("Stable dwell", self.node_stable),
                             ("Retry limit", self.node_iterations), ("Parallel join", self.node_join)):
            form.addRow(label, field)
        apply = QPushButton("Apply node properties")
        apply.clicked.connect(self.apply_node_inspector)
        form.addRow(apply)
        self.node_inspector_fields = [self.node_label, self.node_condition, self.node_completion, self.node_prompt,
                                      self.node_timeout, self.node_poll, self.node_stable, self.node_iterations, self.node_join, apply]
        for field in self.node_inspector_fields:
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(0)
        return scroll

    def _edge_inspector(self):
        panel = QWidget()
        form = QFormLayout(panel)
        self.edge_source = QComboBox()
        self.edge_target = QComboBox()
        self.edge_label = QLineEdit()
        self.edge_condition = QLineEdit()
        self.edge_condition.setPlaceholderText("Blank for outcome-only paths")
        self.edge_outcome = QComboBox()
        self.edge_outcome.addItems(["always", "passed", "failed", "timeout", "warning", "alarm", "skipped"])
        self.edge_default = QCheckBox("Use when no condition matches")
        self.edge_priority = QSpinBox()
        self.edge_priority.setRange(-100000, 100000)
        for label, field in (("From", self.edge_source), ("To", self.edge_target), ("Label", self.edge_label),
                             ("Condition", self.edge_condition), ("Outcome", self.edge_outcome),
                             ("Default", self.edge_default), ("Priority", self.edge_priority)):
            form.addRow(label, field)
        apply = QPushButton("Apply connection properties")
        apply.clicked.connect(self.apply_edge_inspector)
        form.addRow(apply)
        self.edge_inspector_fields = [self.edge_source, self.edge_target, self.edge_label, self.edge_condition,
                                      self.edge_outcome, self.edge_default, self.edge_priority, apply]
        for field in self.edge_inspector_fields:
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(0)
        return scroll

    @staticmethod
    def table(fields):
        table = QTableWidget(0, len(fields))
        table.setHorizontalHeaderLabels([f.replace("_", " ").title() for f in fields])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().hide()
        table.setAlternatingRowColors(True)
        table.setMinimumWidth(120)
        return table

    def sync(self):
        self.loading = True
        try:
            flow = self.owner.draft.data.get("flow") or {}
            self.enable.setEnabled(not flow)
            self.limit.setValue(flow.get("visit_limit", 10000) or 10000)
            for name, table, fields in (("nodes", self.nodes, self.NODE_FIELDS), ("edges", self.edges, self.EDGE_FIELDS)):
                table.setRowCount(len(flow.get(name, ())))
                for r, row in enumerate(flow.get(name, ())):
                    for c, field in enumerate(fields):
                        item = QTableWidgetItem(str(row.get(field, "")))
                        if name == "nodes" and field in {"id", "kind", "step_id"}:
                            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        table.setItem(r, c, item)
            identities = [node.get("id", "") for node in flow.get("nodes", [])]
            for combo in (self.edge_source, self.edge_target):
                current = combo.currentText()
                combo.clear()
                combo.addItems(identities)
                combo.setCurrentText(current)
        finally:
            self.loading = False
        self.load_node_inspector()
        self.load_edge_inspector()

    @staticmethod
    def _enable(fields, enabled):
        for field in fields:
            field.setEnabled(enabled)

    def load_node_inspector(self):
        row = self.nodes.currentRow()
        flow = self.owner.draft.data.get("flow") or {}
        enabled = 0 <= row < len(flow.get("nodes", []))
        self._enable(self.node_inspector_fields, enabled)
        if not enabled:
            self.node_identity.clear()
            self.node_kind.clear()
            return
        node = flow["nodes"][row]
        self.loading = True
        try:
            self.node_identity.setText(node["id"])
            self.node_kind.setText(FLOW_KINDS.get(node["kind"], node["kind"].replace("_", " ").title()))
            self.node_label.setText(node.get("label", ""))
            self.node_condition.setText(node.get("condition", ""))
            self.node_completion.setCurrentText(node.get("completion_mode", "process"))
            self.node_prompt.setText(node.get("operator_prompt", ""))
            self.node_timeout.setValue(float(node.get("timeout_sec", 0) or 0))
            self.node_poll.setValue(float(node.get("poll_sec", .1) or .1))
            self.node_stable.setValue(float(node.get("stable_for_sec", 0) or 0))
            self.node_iterations.setValue(int(node.get("max_iterations", 1) or 1))
            self.node_join.setCurrentText(node.get("join_mode", "and"))
            transition = node["kind"] == "transition"
            for field in (self.node_condition, self.node_completion, self.node_prompt,
                          self.node_timeout, self.node_poll, self.node_stable):
                field.setEnabled(transition)
            self.node_iterations.setEnabled(node["kind"] == "loop")
            self.node_join.setEnabled(node["kind"] == "parallel_join")
        finally:
            self.loading = False

    def load_edge_inspector(self):
        row = self.edges.currentRow()
        flow = self.owner.draft.data.get("flow") or {}
        enabled = 0 <= row < len(flow.get("edges", []))
        self._enable(self.edge_inspector_fields, enabled)
        if not enabled:
            return
        edge = flow["edges"][row]
        self.loading = True
        try:
            self.edge_source.setCurrentText(edge["source"])
            self.edge_target.setCurrentText(edge["target"])
            self.edge_label.setText(edge.get("label", ""))
            self.edge_condition.setText(edge.get("condition", ""))
            self.edge_outcome.setCurrentText(edge.get("outcome", "always"))
            self.edge_default.setChecked(bool(edge.get("is_default", False)))
            self.edge_priority.setValue(int(edge.get("priority", 100)))
        finally:
            self.loading = False

    @guarded
    def apply_node_inspector(self):
        row = self.nodes.currentRow()
        if row < 0:
            return
        node = self.owner.draft.data["flow"]["nodes"][row]
        updated = dict(node, label=self.node_label.text().strip())
        for key in ("condition", "completion_mode", "operator_prompt", "timeout_sec", "poll_sec",
                    "stable_for_sec", "max_iterations", "join_mode"):
            updated.pop(key, None)
        if node["kind"] == "transition":
            updated.update(condition=self.node_condition.text().strip(), completion_mode=self.node_completion.currentText(),
                           operator_prompt=self.node_prompt.text().strip(), timeout_sec=self.node_timeout.value(),
                           poll_sec=self.node_poll.value(), stable_for_sec=self.node_stable.value())
        elif node["kind"] == "loop":
            updated["max_iterations"] = self.node_iterations.value()
        elif node["kind"] == "parallel_join":
            updated["join_mode"] = self.node_join.currentText()
        if updated != node:
            self.owner.checkpoint()
            self.owner.draft.data["flow"]["nodes"][row] = updated
            self.owner.render()
            self.nodes.selectRow(row)

    @guarded
    def apply_edge_inspector(self):
        row = self.edges.currentRow()
        if row < 0:
            return
        edge = self.owner.draft.data["flow"]["edges"][row]
        updated = {"source": self.edge_source.currentText(), "target": self.edge_target.currentText(),
                   "outcome": self.edge_outcome.currentText(), "priority": self.edge_priority.value()}
        if self.edge_label.text().strip():
            updated["label"] = self.edge_label.text().strip()
        if self.edge_condition.text().strip():
            updated["condition"] = self.edge_condition.text().strip()
        if self.edge_default.isChecked():
            updated["is_default"] = True
        if updated != edge:
            self.owner.checkpoint()
            self.owner.draft.data["flow"]["edges"][row] = updated
            self.owner.render()
            self.edges.selectRow(row)

    @guarded
    def add_pattern(self, pattern):
        self.enable_flow()
        kinds = {"decision": ("choice", "merge"), "retry": ("loop", "merge"),
                 "parallel": ("parallel_fork", "parallel_join")}.get(pattern)
        if not kinds:
            raise ValueError("Unknown workflow pattern")
        flow = self.owner.draft.data["flow"]
        used = {node["id"] for node in flow["nodes"]}
        self.owner.checkpoint()
        created = []
        for kind in kinds:
            number, identity = 1, f"{kind}_1"
            while identity in used:
                number += 1
                identity = f"{kind}_{number}"
            used.add(identity)
            row = {"id": identity, "kind": kind, "label": FLOW_KINDS[kind]}
            if kind == "loop":
                row["max_iterations"] = 3
            elif kind == "parallel_join":
                row["join_mode"] = "and"
            flow["nodes"].append(row)
            created.append(identity)
        flow["edges"].append({"source": created[0], "target": created[1],
                              **({"is_default": True} if pattern in {"decision", "retry"} else {})})
        self.owner.render()
        self.locate_node(created[0])
        self.owner.status.setText(f"Added {pattern} pattern. Connect its open ports on the Workflow canvas.")

    @guarded
    def open_example(self):
        if self.owner.allow_discard():
            from azeo_control_trainer.core.procedures.templates import readiness_workflow
            self.owner.checkpoint()
            self.owner.draft = readiness_workflow()
            self.owner.render()
            self.owner.workflow_views.setCurrentIndex(0)
            self.owner.canvas.arrange()
            self.owner.canvas.zoom_fit()

    @guarded
    def enable_flow(self):
        if self.owner.draft.data.get("flow"):
            return
        self.owner.checkpoint()
        self.owner.draft.data["flow"] = linear_flow(self.owner.draft.data["steps"])
        self.owner.render()

    @guarded
    def change_limit(self, value):
        if not self.loading and self.owner.draft.data.get("flow"):
            self.owner.checkpoint()
            self.owner.draft.data["flow"]["visit_limit"] = value
            self.owner.changed()

    @guarded
    def add_node(self, kind, point=None):
        self.enable_flow()
        flow = self.owner.draft.data["flow"]
        used = {n["id"] for n in flow["nodes"]}
        index = 1
        while f"{kind}_{index}" in used:
            index += 1
        identity = f"{kind}_{index}"
        self.owner.checkpoint()
        row = {"id": identity, "kind": kind, "label": FLOW_KINDS[kind]}
        if kind == "transition":
            row.update(condition="False", completion_mode="process", timeout_sec=60, poll_sec=.1, stable_for_sec=0)
        elif kind == "loop":
            row["max_iterations"] = 3
        elif kind == "parallel_join":
            row["join_mode"] = "and"
        flow["nodes"].append(row)
        if point is not None:
            self.owner.draft.data.setdefault("metadata", {}).setdefault("canvas_layout", []).append(
                {"step_id": "flow:" + identity, "x": point.x(), "y": point.y()})
        self.owner.render()
        self.owner.workflow_views.setCurrentIndex(0)

    @guarded
    def add_edge(self, source=None, target=None):
        self.enable_flow()
        flow = self.owner.draft.data["flow"]
        if isinstance(source, str) and isinstance(target, str):
            source = self.owner.canvas.flow_ids.get(source, source)
            target = self.owner.canvas.flow_ids.get(target, target)
        else:
            source, target = flow["nodes"][0]["id"], flow["nodes"][-1]["id"]
        if any(e["source"] == source and e["target"] == target for e in flow["edges"]):
            self.locate_edge(source, target)
            return
        self.owner.checkpoint()
        node = next(n for n in flow["nodes"] if n["id"] == source)
        edge = {"source": source, "target": target, "outcome": "always", "priority": 100}
        if node["kind"] in {"choice", "loop"}:
            if any(e["source"] == source and e.get("is_default") for e in flow["edges"]):
                edge["condition"] = "False"
            else:
                edge["is_default"] = True
        flow["edges"].append(edge)
        self.owner.render()

    @guarded
    def edit(self, name, row, col):
        if self.loading:
            return
        table, fields = (self.nodes, self.NODE_FIELDS) if name == "nodes" else (self.edges, self.EDGE_FIELDS)
        key = fields[col]
        value = table.item(row, col).text().strip()
        if key in {"timeout_sec", "poll_sec", "stable_for_sec"} and value:
            value = float(value)
        elif key in {"max_iterations", "priority"} and value:
            value = int(value)
        elif key == "is_default":
            if value.lower() not in {"", "true", "false"}:
                raise ValueError("Default must be True or False")
            value = value.lower() == "true"
        self.owner.checkpoint()
        record = self.owner.draft.data["flow"][name][row]
        if value == "":
            record.pop(key, None)
        else:
            record[key] = value
        self.owner.canvas.sync(self.owner.draft.data["steps"], self.owner.draft.data.get("metadata", {}), self.owner.draft.data["flow"])
        self.owner.changed()

    @guarded
    def remove_node(self):
        row = self.nodes.currentRow()
        if row < 0:
            return
        flow = self.owner.draft.data["flow"]
        node = flow["nodes"][row]
        if node["kind"] == "start":
            raise ValueError("A procedure requires its Start node")
        self.owner.checkpoint()
        flow["nodes"].pop(row)
        flow["edges"] = [e for e in flow["edges"] if node["id"] not in (e["source"], e["target"])]
        if node.get("step_id"):
            self.owner.draft.data["steps"] = [s for s in self.owner.draft.data["steps"] if s["id"] != node["step_id"]]
        self.owner.render()

    @guarded
    def remove_edge(self):
        row = self.edges.currentRow()
        if row >= 0:
            self.owner.checkpoint()
            self.owner.draft.data["flow"]["edges"].pop(row)
            self.owner.render()

    def locate_edge(self, source, target):
        flow = self.owner.draft.data.get("flow") or {}
        for row, edge in enumerate(flow.get("edges", ())):
            if (edge["source"], edge["target"]) == (source, target):
                self.owner.workflow_views.setCurrentWidget(self)
                self.edges.selectRow(row)
                self.edges.scrollToItem(self.edges.item(row, 0))
                return

    def locate_node(self, key):
        identity = self.owner.canvas.flow_ids.get(key, key)
        for row, node in enumerate(self.owner.draft.data.get("flow", {}).get("nodes", ())):
            if node["id"] == identity:
                self.owner.workflow_views.setCurrentWidget(self)
                self.nodes.selectRow(row)
                self.nodes.scrollToItem(self.nodes.item(row, 0))
                return

    # The shared guarded command boundary expects these owner surfaces.
    @property
    def status(self):
        return self.owner.status

    @property
    def review(self):
        return self.owner.review

    @property
    def tabs(self):
        return self.owner.tabs


def reconcile_actions(data):
    """Editing a step never silently changes the other authored branch edges."""
    flow = data.get("flow")
    if not flow:
        return
    ids = {s["id"] for s in data["steps"]}
    removed = {n["id"] for n in flow["nodes"] if n.get("step_id") and n["step_id"] not in ids}
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] not in removed]
    flow["edges"] = [e for e in flow["edges"] if not {e["source"], e["target"]} & removed]
    existing = {n.get("step_id") for n in flow["nodes"]}
    flow["nodes"].extend({"id": "step:" + s["id"], "kind": "action", "step_id": s["id"]}
                         for s in data["steps"] if s["id"] not in existing)
