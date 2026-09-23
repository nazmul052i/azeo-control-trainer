"""Review class consumers and incompatible instance choices before a save."""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import PVM_SCOPE_NAMES

import copy
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QVBoxLayout

from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration
from azeo_control_trainer.core.hmi.pvms.publishing import displays_using_class
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)
from azeo_control_trainer.core.presentation.dialog_layout import scrolling_body


def affected_instances(name, documents):
    matches = []
    for display_name, document in documents.items():
        if display_name.startswith("_pvm_"):
            continue
        seen = set()
        for item in document.get("pvms", []):
            if displays_using_class(name, [{"display": display_name, "pvms": [item]}]):
                matches.append((display_name, item["id"], dict(item.get("choices", {}))))
        for item in document.get("items", []):
            if not displays_using_class(name, [{"display": display_name, "items": [item]}]):
                continue
            definition = item.get("instance_definition") or item.get("user_pvm")
            identity = item.get("instance_id") or item.get("group") or item["id"]
            if identity in seen:
                continue
            seen.add(identity)
            choices = dict(item.get("instance_choices") or item.get("pvm_choices") or {}) if definition == name else {"__nested_consumer__": True}
            matches.append((display_name, item["id"], choices))
    return matches


def choice_issues(before, after, choices):
    issues = []
    for name, value in choices.items():
        old, new = before.property(name), after.property(name)
        if new is None and old is not None:
            issues.append(f"Removed property: {name} (instance value {value})")
        elif new is not None and new.ptype == "Selection" and new.option(value) is None:
            issues.append(f"Removed option: {name} = {value}")
        elif old is not None and new is not None and old.ptype != new.ptype:
            issues.append(f"Changed type: {name}: {old.ptype} → {new.ptype}")
    return issues


def removed_references(before, after, layout):
    removed = {prop.name for prop in before.all_properties()} - {prop.name for prop in after.all_properties()}
    references = set(re.findall(r"\b(?:" + "|".join(PVM_SCOPE_NAMES) + r")\.([A-Za-z_]\w*)",
                                json.dumps(layout)))
    return [f"Drawing still references removed property: Pvm.{name}" for name in sorted(removed & references)]


class ImpactDialog(QDialog):
    def showEvent(self, event):  # noqa: N802
        from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
        fit_dialog_to_screen(self)
        super().showEvent(event)

    def __init__(self, designer, name=None, *, saving=False):
        super().__init__(designer)
        self.designer = designer
        self.name = name or designer.current_class
        self.before = PvmConfiguration.from_dict(json.loads(designer._saved_states[self.name]))
        self.after = copy.deepcopy(designer.configs[self.name])
        self.setWindowTitle(f"Change impact · {self.name}")
        self.resize(1120, 740)
        root = scrolling_body(self)
        add_authoring_dialog_header(
            self,
            root,
            "Configuration change impact",
            f"Review consumers and incompatible instance choices for {self.name}.",
        )
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.documents = {}
        unreadable = {}
        for path in designer.standards_root.glob("*/draft.json"):
            try:
                self.documents[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                unreadable[path.parent.name] = f"Cannot review {path.parent.name}: {error}"
        host = designer.parent()
        if callable(getattr(host, "studios", None)):
            for studio in host.studios():
                self.documents[studio.display.name] = studio._document()
                unreadable.pop(studio.display.name, None)
        blockers = list(unreadable.values())
        self.instances = []
        for display_name, document in self.documents.items():
            try:
                self.instances.extend(affected_instances(self.name, {display_name: document}))
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                # A damaged draft makes its consumers unknown, not absent.
                # Keep the review open and refuse Save instead of leaking an
                # exception through the designer's Qt button callback.
                blockers.append(f"Cannot review {display_name}: {error}")
        self.table = QTableWidget(len(self.instances), 3)
        self.table.setHorizontalHeaderLabels(("Affected display", "Instance", "Compatibility"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.refresh)
        root.addWidget(self.table, 1)
        blockers.extend(removed_references(self.before, self.after,
                        designer._user_library().entries.get(self.name, {})))
        for row, (display, identity, choices) in enumerate(self.instances):
            issues = choice_issues(self.before, self.after, choices)
            blockers.extend(issues)
            compatibility = "Nested consumer · preview class defaults" if choices.get("__nested_consumer__") else "Compatible choices"
            for column, value in enumerate((display, identity, "; ".join(issues) or compatibility)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.preview_labels = []
        previews = QHBoxLayout()
        for caption in ("Saved class", "Proposed class"):
            column = QVBoxLayout()
            heading = QLabel(caption)
            heading.setAlignment(Qt.AlignCenter)
            column.addWidget(heading)
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(170)
            column.addWidget(label)
            previews.addLayout(column, 1)
            self.preview_labels.append(label)
        root.addLayout(previews)
        self.values = QTableWidget(0, 3)
        self.values.setHorizontalHeaderLabels(("Resolved property", "Saved", "Proposed"))
        self.values.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.values.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.values, 1)
        self.status.setText(f"{len(self.instances)} instances in {len({one[0] for one in self.instances})} displays. "
                            "Select an instance to compare its choices. Save updates the class; affected displays still need verification and publishing. "
                            + ("\n" + "\n".join(dict.fromkeys(blockers)) if blockers else ""))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel if saving else QDialogButtonBox.Close)
        if saving:
            buttons.button(QDialogButtonBox.Save).setEnabled(not blockers)
            buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(
            buttons, QDialogButtonBox.Save if saving else None)
        root.addWidget(buttons)
        self.blockers = blockers
        self.refresh()

    def refresh(self):
        from ..component_icons import authored_preview
        from .designer import _coded_class_preview
        from azeo_control_trainer.core.hmi.pvms.typography import resolve_typography
        row = self.table.currentRow()
        choices = self.instances[row][2] if row >= 0 else {}
        library = self.designer._user_library()
        for label, config in zip(self.preview_labels, (self.before, self.after)):
            try:
                if self.name in library.entries:
                    pixmap = authored_preview(library, self.name, config, 460, 180, choices=choices)
                else:
                    cls = next((cls for cls in registry.all_classes().values() if cls.__name__ == self.name), None)
                    if cls is None:
                        label.setText("Property configuration only · no drawing")
                        continue
                    pixmap = _coded_class_preview(cls, 460, 180, typography=resolve_typography(config, choices), strict=True)
                label.setPixmap(pixmap)
            except Exception as error:  # noqa: BLE001 - broken drafts remain reviewable
                label.setText(f"Preview unavailable: {error}")
        before, after = self.before.resolved(choices), self.after.resolved(choices)
        keys = sorted(before.keys() | after.keys())
        keys = [key for key in keys if before.get(key) != after.get(key)]
        self.values.setRowCount(len(keys))
        for row, key in enumerate(keys):
            for column, value in enumerate((key, before.get(key, "Absent"), after.get(key, "Absent"))):
                self.values.setItem(row, column, QTableWidgetItem(str(value)))
