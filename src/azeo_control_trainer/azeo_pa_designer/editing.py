"""Block editing commands shared by menus, canvas keys and the step list."""
from copy import deepcopy
from functools import wraps
from html import escape
import json
import logging

from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.menu_style import studio_menu
from azeo_control_trainer.core.presentation.studio_icons import studio_icon
from azeo_control_trainer.core.procedures.library import block_for_step, block_for_token, block_library

CLIPBOARD_MIME = "application/x-azeo-procedure-selection+json"


def guarded(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            logging.getLogger(__name__).exception("Procedure authoring command failed")
            message = str(error) or type(error).__name__
            self.status.setText(message.splitlines()[0][:240] + " · See Review for details.")
            self.review.setPlainText(message)
            self.tabs.setCurrentWidget(self.review)
            if method.__name__ != "update_problems" and hasattr(self, "update_problems"):
                self.update_problems()
            return False
    return invoke


class BlockEditing:
    def selected_flow_ids(self):
        if not self.draft.data.get("flow") or self.workflow_views.currentIndex() != 0:
            return set()
        return {self.canvas.flow_ids[item.node_id] for item in self.canvas.scene.selectedItems()
                if hasattr(item, "node_id") and item.node_id in self.canvas.flow_ids}

    def selected_step_ids(self):
        if self.workflow_views.currentIndex() == 1:
            row = self.steps.currentRow()
            return [self.draft.data["steps"][row]["id"]] if row >= 0 else []
        selected = {getattr(item, "step_id", None) for item in self.canvas.scene.selectedItems()}
        return [step["id"] for step in self.draft.data["steps"] if step["id"] in selected]

    def install_block_commands(self, edit_menu):
        self.block_actions = {}
        self._block_shortcuts = []
        for command, label, key in (
            ("open", "Properties...", "Alt+Return"), ("rename", "Rename...", "F2"),
            ("cut", "Cut", "Ctrl+X"), ("copy", "Copy", "Ctrl+C"),
            ("paste", "Paste", "Ctrl+V"), ("duplicate", "Duplicate", "Ctrl+D"),
            ("delete", "Delete Block", "Delete"), ("select_all", "Select All", "Ctrl+A"),
        ):
            action = edit_menu.addAction(label + "\t" + key)
            action.triggered.connect(lambda _checked=False, c=command: self.block_command(c))
            self.block_actions[command] = action
            # Widget-scoped shortcuts leave text editors' native clipboard alone.
            # Keep wrappers alive: Qt owns the actions, Python owns their handlers.
            for view in (self.canvas, self.steps):
                shortcut = QAction(view)
                shortcut.setShortcut(QKeySequence(key))
                shortcut.setShortcutContext(Qt.WidgetShortcut)
                shortcut.triggered.connect(lambda _checked=False, c=command: self.block_command(c))
                view.addAction(shortcut)
                self._block_shortcuts.append(shortcut)
        self.canvas.context_requested.connect(self.canvas_context_menu)
        self.steps.setContextMenuPolicy(Qt.CustomContextMenu)
        self.steps.customContextMenuRequested.connect(self.step_list_context_menu)
        self.block_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.block_tree.customContextMenuRequested.connect(self.palette_context_menu)
        QApplication.clipboard().dataChanged.connect(self.update_block_actions)
        self._clipboard_connected = True
        edit_menu.aboutToShow.connect(self.update_block_actions)

    @guarded
    def update_block_actions(self):
        if not hasattr(self, "block_actions") or getattr(self, "_closing", False):
            return
        ids = self.selected_step_ids()
        for command, action in self.block_actions.items():
            enabled = bool(ids or self.selected_flow_ids())
            if command in {"open", "rename"}:
                enabled = len(ids) == 1
            elif command == "paste":
                mime = QApplication.clipboard().mimeData()
                enabled = mime is not None and mime.hasFormat(CLIPBOARD_MIME)
            elif command == "select_all":
                enabled = bool(self.draft.data["steps"])
            action.setEnabled(enabled)

    @guarded
    def block_command(self, command):
        ids = self.selected_step_ids()
        if command == "select_all":
            self.workflow_views.setCurrentIndex(0)
            self.canvas.select_steps([step["id"] for step in self.draft.data["steps"]])
            if self.draft.data.get("flow"):
                for node in self.canvas.nodes.values():
                    node.setSelected(True)
        elif command == "paste":
            mime = QApplication.clipboard().mimeData()
            if mime is not None and mime.hasFormat(CLIPBOARD_MIME):
                raw = bytes(mime.data(CLIPBOARD_MIME))
                if len(raw) > 2_000_000:
                    raise ValueError("Procedure clipboard selection is too large")
                self.paste_blocks(json.loads(raw))
        elif ids or self.selected_flow_ids():
            if command in {"open", "rename"}:
                self.tabs.setCurrentIndex(0)
                self.select_canvas_step(ids[0])
                self.property_filter.clear()
                field = self.fields["id" if command == "rename" else "description"]
                self.property_groups["Quick Configuration"].set_collapsed(False)
                self.inspector.ensureWidgetVisible(field)
                field.setFocus()
                if command == "rename":
                    field.selectAll()
            elif command in {"copy", "cut", "duplicate"}:
                payload = self.copy_blocks(ids)
                if command == "duplicate":
                    self.paste_blocks(payload)
                else:
                    mime = QMimeData()
                    mime.setData(CLIPBOARD_MIME, json.dumps(payload).encode("utf-8"))
                    QApplication.clipboard().setMimeData(mime)
                    if command == "cut":
                        self.delete_blocks(ids)
            elif command == "delete":
                self.delete_blocks(ids)

    def copy_blocks(self, ids):
        keys = {self.canvas.key(step_id) for step_id in ids}
        payload = {"version": 1,
                "steps": deepcopy([step for step in self.draft.data["steps"] if step["id"] in ids]),
                "layout": [row for row in self.canvas.current_layout() if row["step_id"] in ids],
                "routes": deepcopy([row for row in self.draft.data.get("metadata", {}).get("canvas_routes", [])
                                    if row["source"] in keys and row["target"] in keys])}
        if self.draft.data.get("flow"):
            selected = self.selected_flow_ids()
            selected.update(n["id"] for n in self.draft.data["flow"]["nodes"] if n.get("step_id") in ids)
            selected.difference_update(n["id"] for n in self.draft.data["flow"]["nodes"] if n["kind"] == "start")
            payload["layout"].extend(row for row in self.canvas.current_layout() if row["step_id"].removeprefix("flow:") in selected
                                     and row["step_id"].startswith("flow:"))
            payload["flow_fragment"] = deepcopy({
                "nodes": [n for n in self.draft.data["flow"]["nodes"] if n["id"] in selected and n["kind"] != "start"],
                "edges": [e for e in self.draft.data["flow"]["edges"] if e["source"] in selected and e["target"] in selected]})
        return payload

    def paste_blocks(self, payload):
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Unsupported procedure clipboard format")
        copied = payload.get("steps")
        if not isinstance(copied, list) or not (0 if payload.get("flow_fragment") else 1) <= len(copied) <= 1000:
            raise ValueError("Select between 1 and 1000 procedure blocks to paste")
        # Check the whole payload before checkpointing or changing the document.
        # Incomplete field edits may travel; Validate still checks their contract.
        ids = set()
        for step in copied:
            if not isinstance(step, dict) or not isinstance(step.get("id"), str) or step["id"] in ids:
                raise ValueError("Invalid procedure clipboard block identity")
            if step.get("type") not in {block.step_type for block in block_library()}:
                raise ValueError("Unsupported block type in procedure clipboard")
            ids.add(step["id"])
        from azeo_control_trainer.core.procedures.model import AdvisoryMetadata
        fragment = payload.get("flow_fragment")
        if fragment:
            from azeo_control_trainer.core.procedures.flow import AdvisoryFlowNode
            from azeo_control_trainer.core.pa_designer.core.procedure_model import FlowEdge
            if len(fragment.get("nodes", ())) > 1000 or len(fragment.get("edges", ())) > 2000:
                raise ValueError("Workflow clipboard fragment is too large")
            fragment = {"nodes": [AdvisoryFlowNode.model_validate(n).model_dump(mode="json") for n in fragment["nodes"]],
                        "edges": [FlowEdge.model_validate(e).model_dump(mode="json") for e in fragment["edges"]]}
            node_ids = {n["id"] for n in fragment["nodes"]}
            if len(node_ids) != len(fragment["nodes"]) or any(
                    n["kind"] == "start" or (n.get("step_id") and n["step_id"] not in ids) for n in fragment["nodes"]):
                raise ValueError("Invalid workflow clipboard identities or action references")
            if any(e["source"] not in node_ids or e["target"] not in node_ids for e in fragment["edges"]):
                raise ValueError("Clipboard connection references a missing workflow node")
        geometry = AdvisoryMetadata(canvas_layout=payload.get("layout", []), canvas_routes=payload.get("routes", []))
        points = {row.step_id: (row.x, row.y) for row in geometry.canvas_layout}
        steps = self.draft.data["steps"]
        if len(steps) + len(copied) > 4998:
            raise ValueError("The visual procedure supports at most 4998 steps plus Start and End")
        selected = set(self.selected_step_ids())
        position = max((i + 1 for i, step in enumerate(steps) if step["id"] in selected), default=max(0, self._step_row + 1))
        if steps and steps[-1]["type"] == "complete":
            position = min(position, len(steps) - 1)
        used = {step["id"] for step in steps}
        inserted, layout, remap = [], [], {}
        for source in copied:
            step = deepcopy(source)
            index = 1
            while f"{source['id']}_{index}" in used:
                index += 1
            step["id"] = f"{source['id']}_{index}"
            used.add(step["id"])
            remap[self.canvas.key(source["id"])] = self.canvas.key(step["id"])
            inserted.append(step)
            if source["id"] in points:
                x, y = points[source["id"]]
                point = self.canvas.constrain_scene_point(QPointF(x + 36, y + 36))
                layout.append({"step_id": step["id"], "x": round(point.x()), "y": round(point.y())})
        routes = []
        for route in geometry.canvas_routes:
            if route.source in remap and route.target in remap:
                route_points = [self.canvas.constrain_scene_point(QPointF(point.x + 36, point.y + 36)) for point in route.points]
                routes.append({"source": remap[route.source], "target": remap[route.target],
                               "points": [{"x": point.x(), "y": point.y()} for point in route_points]})
        self.checkpoint()
        steps[position:position] = inserted
        if fragment:
            from azeo_control_trainer.core.procedures.flow import linear_flow
            flow = self.draft.data.setdefault("flow", None)
            if not flow:
                flow = linear_flow([s for s in steps if s not in inserted])
                self.draft.data["flow"] = flow
            used_nodes = {n["id"] for n in flow["nodes"]}
            node_remap = {}
            for node in fragment["nodes"]:
                old = node["id"]
                number = 1
                while f"{old}_{number}" in used_nodes:
                    number += 1
                node["id"] = f"{old}_{number}"
                used_nodes.add(node["id"])
                node_remap[old] = node["id"]
                if node.get("step_id"):
                    node["step_id"] = remap[self.canvas.key(node["step_id"])].removeprefix("step:")
                elif "flow:" + old in points:
                    x, y = points["flow:" + old]
                    layout.append({"step_id": "flow:" + node["id"], "x": x + 36, "y": y + 36})
                flow["nodes"].append(node)
            flow["edges"].extend(dict(edge, source=node_remap[edge["source"]], target=node_remap[edge["target"]])
                                 for edge in fragment["edges"] if edge["source"] in node_remap and edge["target"] in node_remap)
        metadata = self.draft.data.setdefault("metadata", {})
        metadata.setdefault("canvas_layout", []).extend(layout)
        metadata.setdefault("canvas_routes", []).extend(routes)
        self._prune_canvas_metadata()
        self._step_row = position
        self.render()
        self.canvas.select_steps([step["id"] for step in inserted])

    def delete_blocks(self, ids):
        selected = self.selected_flow_ids()
        if not ids and not selected:
            return
        self.checkpoint()
        self.draft.data["steps"] = [step for step in self.draft.data["steps"] if step["id"] not in ids]
        flow = self.draft.data.get("flow")
        if flow:
            selected.update(n["id"] for n in flow["nodes"] if n.get("step_id") in ids)
            selected.difference_update(n["id"] for n in flow["nodes"] if n["kind"] == "start")
            flow["nodes"] = [n for n in flow["nodes"] if n["id"] not in selected]
            flow["edges"] = [e for e in flow["edges"] if e["source"] not in selected and e["target"] not in selected]
        self._prune_canvas_metadata()
        self.render()

    def _show_context(self, menu, position):
        previous = getattr(self, "_context_menu", None)
        if previous:
            previous.close()
            previous.deleteLater()
        self._context_menu = menu
        if not is_headless():
            menu.popup(position)

    def block_menu(self, step_id):
        step = next(step for step in self.draft.data["steps"] if step["id"] == step_id)
        block = block_for_step(step)
        ids = self.selected_step_ids()
        menu = studio_menu(f"{escape(block.label)}  {escape(step_id)}", "Procedure block" if len(ids) == 1 else f"{len(ids)} selected blocks", self)
        self.update_block_actions()
        for command in ("open", "rename"):
            menu.addAction(self.block_actions[command])
        menu.addSeparator()
        clipboard = menu.addMenu("Clipboard")
        for command in ("cut", "copy", "paste", "duplicate"):
            clipboard.addAction(self.block_actions[command])
        menu.addSeparator()
        menu.addAction(self.block_actions["delete"])
        menu.addSeparator()
        if len(ids) == 1:
            menu.addAction("Find usages / Safe rename...").triggered.connect(
                lambda: self.open_refactor(kind="step", symbol=step_id)
            )
        if ids:
            extract = menu.addAction("Extract reusable procedure...")
            extract.setEnabled(not bool(self.draft.data.get("flow")))
            extract.triggered.connect(self.extract_reusable)
        menu.addSeparator()
        menu.addAction(studio_icon("comment", 16), "Block Help").triggered.connect(lambda: self.show_block_reference(block.help_key))
        return menu

    def _add_block_menu(self, menu, point, pair=None):
        categories = {}
        for block in block_library():
            if block.category not in categories:
                categories[block.category] = menu.addMenu("Add " + block.category)
            action = categories[block.category].addAction(studio_icon(block.icon, 16), block.label)
            action.triggered.connect(lambda _checked=False, k=block.block_id:
                                     self.drop_canvas_block(k, point, pair))

    @guarded
    def canvas_context_menu(self, target, position, point):
        kind, identity = target
        if kind == "flow":
            menu = studio_menu("WORKFLOW NODE", identity, self)
            menu.addAction("Properties...").triggered.connect(lambda: self.flow_editor.locate_node(identity))
            self.update_block_actions()
            for command in ("copy", "cut", "duplicate", "delete"):
                menu.addAction(self.block_actions[command])
            menu.addAction("Block Help").triggered.connect(lambda: self.open_help_topic("advanced_workflow"))
        elif kind == "annotation":
            menu = studio_menu("ENGINEERING ANNOTATION", identity, self)
            menu.addAction("Edit annotation...").triggered.connect(lambda: self.edit_annotation(identity))
            menu.addAction("Delete annotation").triggered.connect(lambda: self.remove_annotation(identity))
        elif kind == "block":
            menu = self.block_menu(identity)
        elif kind == "wire":
            menu = studio_menu("CONNECTION", "Workflow execution path", self)
            if self.draft.data.get("flow"):
                menu.addAction("Connection Properties...").triggered.connect(lambda:
                    self.flow_editor.locate_edge(*(self.canvas.flow_ids[k] for k in identity)))
            reset = menu.addAction("Auto Route")
            reset.setEnabled(self.canvas.edges[identity].custom_route)
            reset.triggered.connect(lambda: self.reset_canvas_route(*identity))
            menu.addSeparator()
            self._add_block_menu(menu, point, identity)
        else:
            menu = studio_menu("PROCEDURE", escape(self.draft.data.get("name", "")), self)
            self._add_block_menu(menu, point)
            from .flow_editor import FLOW_KINDS
            flow_menu = menu.addMenu("Add workflow node")
            for key, label in FLOW_KINDS.items():
                flow_menu.addAction(label).triggered.connect(lambda _checked=False, k=key: self.flow_editor.add_node(k, point))
            menu.addAction("Add engineering annotation...").triggered.connect(
                lambda: self.add_annotation(point=point)
            )
            menu.addSeparator()
            self.update_block_actions()
            for action in (self.undo_action, self.redo_action, self.block_actions["paste"], self.block_actions["select_all"]):
                menu.addAction(action)
            menu.addSeparator()
            menu.addAction("Zoom to Fit").triggered.connect(self.canvas.zoom_fit)
            menu.addAction("Auto Arrange").triggered.connect(self.canvas.arrange)
        self._show_context(menu, position)

    @guarded
    def step_list_context_menu(self, point):
        row = self.steps.rowAt(point.y())
        if row >= 0:
            self.steps.selectRow(row)
            menu = self.block_menu(self.draft.data["steps"][row]["id"])
            self._show_context(menu, self.steps.viewport().mapToGlobal(point))

    @guarded
    def palette_context_menu(self, point):
        item = self.block_tree.itemAt(point)
        token = item.data(0, Qt.UserRole) if item else None
        if not token:
            return
        self.block_tree.setCurrentItem(item)
        block = block_for_token(token)
        menu = studio_menu(escape(block.label), f"{block.version} · {escape(block.category)}", self)
        menu.addAction("Insert into procedure").triggered.connect(self.add_selected_block)
        menu.addAction(studio_icon("comment", 16), "Block Help").triggered.connect(lambda: self.show_block_reference(block.help_key))
        self._show_context(menu, self.block_tree.viewport().mapToGlobal(point))

    @guarded
    def show_block_reference(self, kind):
        self.open_help_topic(kind)
