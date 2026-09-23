"""Batch instance engineering over the same document and validation services."""
from __future__ import annotations

import copy

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem

from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.hmi.pvms.engineering import remap_controls
from .engineering_tools import EngineeringDialog


class WorksheetDialog(EngineeringDialog):
    def __init__(self, studio):
        super().__init__(studio, "Engineering worksheet")
        top = QHBoxLayout()
        self.scope = AuthoringComboBox()
        self.scope.addItems(("Selected objects", "Whole display"))
        top.addWidget(self.scope)
        self.button(top, "Reload", self.reload)
        self.root.addLayout(top)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("Object", "Parameter", "Tag / display target", "Label", "Variant", "Before", "Validation"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(2, 240)
        self.table.setColumnWidth(5, 210)
        self.table.setAlternatingRowColors(True)
        self.root.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.button(bottom, "Preview changes", self.preview)
        self.apply_button = self.button(
            bottom, "Apply all", self.apply, primary=True)
        self.root.addLayout(bottom)
        self.table.itemChanged.connect(self.invalidate)
        self.scope.currentIndexChanged.connect(self.reload)
        self.reload()

    def invalidate(self, *_):
        self._candidate = None
        self.apply_button.setEnabled(False)

    def reload(self):
        self.invalidate()
        self._document = copy.deepcopy(self.studio._document())
        selected = {self.studio._endpoint_id(item) for item in self.studio.selection.snapshot().items}
        self.records = []
        self.instances = {}
        seen_groups = set()
        self.table.setRowCount(0)
        for family in ("pvms", "items"):
            for index, data in enumerate(self._document.get(family, [])):
                if self.scope.currentIndex() == 0 and data.get("id") not in selected:
                    continue
                if data.get("locked"):
                    continue
                group = data.get("group")
                if family == "items" and (data.get("instance_definition") or data.get("user_pvm")):
                    if group in seen_groups:
                        continue
                    seen_groups.add(group)
                    state = self.studio._user_instance_state(group)
                    if state is None:
                        continue
                    config = self.studio._config_for_name(state["name"])
                    if config is None:
                        continue
                    self.instances[group] = state
                    for prop in config.public_properties():
                        if not config.is_present(prop.name, state["choices"]):
                            continue
                        value = state["choices"].get(prop.name, prop.default)
                        row = self.table.rowCount()
                        self.table.insertRow(row)
                        self.records.append(("instance", group, prop.name, False))
                        for col, text in enumerate((state["name"], prop.name, value, "Class property", "", value, "Not checked")):
                            cell = QTableWidgetItem(str(text))
                            if col != 2:
                                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                            self.table.setItem(row, col, cell)
                    continue
                params = data.get("params", {}) if family == "pvms" else {
                    key: data[key] for key in ("path", "target") if key in data}
                # Static labels are useful even when they have no binding.
                for n, (key, value) in enumerate(params.items() or [("", "")]):
                    row = self.table.rowCount()
                    self.table.insertRow(row)
                    self.records.append((family, index, key, n == 0))
                    label_key = "label" if family == "pvms" else "text"
                    values = (data.get("id", ""), key, value, data.get(label_key, ""), data.get("variant", ""), value, "Not checked")
                    for col, text in enumerate(values):
                        cell = QTableWidgetItem(str(text))
                        if col in (0, 1, 5, 6) or col == 2 and not key or col in (3, 4) and n:
                            cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                        if col == 4 and family != "pvms":
                            cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                        self.table.setItem(row, col, cell)
        self.status.setText(f"{len(self.records)} rows. Edit tags, labels and variants; display links use their existing target field. Locked objects are excluded.")

    def preview(self):
        self.invalidate()
        if self.studio._document() != self._document:
            raise ValueError("The canvas changed. Reload the worksheet first.")
        candidate = copy.deepcopy(self._document)
        instance_choices = {group: dict(state["choices"]) for group, state in self.instances.items()}
        errors = []
        names = {path.parent.name for path in self.studio.store.root.glob("*/draft.json")}
        names.add(self.studio.display.name)
        # Exception classes can have several parameters. Validate their final
        # combined mapping, never a half-edited row against an old partner.
        for row, (family, index, key, _first) in enumerate(self.records):
            if family == "pvms" and key:
                candidate[family][index]["params"][key] = self.table.item(row, 2).text().strip()
        for row, (family, index, key, first) in enumerate(self.records):
            value = self.table.item(row, 2).text().strip()
            try:
                if family == "instance":
                    config = self.studio._config_for_name(self.instances[index]["name"])
                    prop = config.property(key)
                    if prop.ptype == "Selection" and prop.option(value) is None:
                        raise ValueError("Choose one of: " + ", ".join(option.name for option in prop.options))
                    if prop.required and not value:
                        raise ValueError("Required property")
                    if prop.ptype == "Procedure Reference":
                        from azeo_control_trainer.core.procedures.hmi import reference
                        reference(value)
                    if value == prop.default:
                        instance_choices[index].pop(key, None)
                    else:
                        instance_choices[index][key] = value
                    self.table.item(row, 6).setText("Ready")
                    continue
                data = candidate[family][index]
                if first:
                    label_key = "label" if family == "pvms" else "text"
                    label = self.table.item(row, 3).text()
                    if label or label_key in data:
                        data[label_key] = label
                if family == "pvms":
                    if first:
                        variant = self.table.item(row, 4).text().strip()
                        if variant:
                            data["variant"] = variant
                        else:
                            data.pop("variant", None)
                    block, _, role = data["class"].partition("/")
                    cls = registry.get(block, role, data.get("variant", ""))
                    if cls is None:
                        raise ValueError("Unknown PVM variant")
                    if key:
                        data["params"][key] = value
                    remap_controls({"pvms": [data]}, {}, self.studio.graphs_provider())
                    specs = self.studio.renderer.class_bindings(cls)
                    config = self.studio.renderer.pvm_config(cls)
                    active = config.plan_bindings(specs, data.get("choices", {}), data.get("params", {}))[0] if config else [
                        (binding, data.get("params", {})) for binding in specs]
                    from azeo_control_trainer.core.hmi.binding.engine import format_template
                    for binding, params in active:
                        if binding.path:
                            from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
                            path = format_template(binding.path, params)
                            if self.studio.preview_source.source.read(path) is UNRESOLVED:
                                raise ValueError(f"Unresolved binding: {path}")
                elif key == "target":
                    if value not in names:
                        raise ValueError("Choose an existing display")
                    data[key] = value
                elif key:
                    data[key] = value
                    remap_controls({"items": [data]}, {}, self.studio.graphs_provider())
                self.table.item(row, 6).setText("Ready")
            except (ValueError, KeyError) as error:
                self.table.item(row, 6).setText(str(error))
                errors.append(f"Row {row + 1}: {error}")
        if errors:
            self.status.setText("\n".join(errors))
            return None
        self._instance_changes = {}
        for group, choices in instance_choices.items():
            state = self.instances[group]
            if choices == state["choices"]:
                continue
            config = self.studio._config_for_name(state["name"])
            records = self.studio.user_library().instantiate(state["name"], *state["origin"], config=config,
                        choices=choices, standards=self.studio._standards_lookup, overrides=state["overrides"])
            remap_controls({"items": records}, {}, self.studio.graphs_provider())
            self._instance_changes[group] = choices
        self._candidate = candidate
        self.apply_button.setEnabled(bool(self.records) and (candidate != self._document or bool(self._instance_changes)))
        self.status.setText("All rows validated. Apply all creates one undo step.")
        return candidate

    def apply(self):
        if self._candidate is None:
            raise ValueError("Preview all rows before applying")
        # Commissioning fingerprints intentionally omit evidence and TEST
        # sequences. A document replacement must preserve those edits too.
        if self.studio._document() != self._document:
            self.invalidate()
            raise ValueError("The canvas changed. Reload and preview again.")
        self.editable()
        if self.studio._document() != self._document:
            self.invalidate()
            raise ValueError("The saved display changed while opening it for editing. Reload the worksheet.")
        previous_redo = list(self.studio._redo_stack)
        previous_unsaved = self.studio.unsaved
        self.studio.checkpoint()
        try:
            self.studio._load_document(self._candidate)
            for group, choices in self._instance_changes.items():
                self.studio._rebuild_user_instance(group, choices=choices, checkpoint=False, mark=False)
            self.studio.mark_unsaved()
        except Exception:
            self.studio._load_document(copy.deepcopy(self._document))
            self.studio._undo_stack.pop()
            self.studio._redo_stack[:] = previous_redo
            self.studio.unsaved = previous_unsaved
            self.studio._sync_status()
            raise
        self.reload()
        self.status.setText("Applied all changes. Undo restores the complete previous display.")
