"""Semantic changes alongside the same read-only renderer used by Operator Live."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QTableWidget, QTableWidgetItem, QHeaderView

from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
from .engineering_tools import EngineeringDialog


def semantic_changes(before, after):
    """Return addressable changes, including bindings nested inside actions."""
    changes = []

    def walk(a, b, identity, path):
        if a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                walk(a.get(key), b.get(key), identity, f"{path}.{key}".strip("."))
        elif isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                walk(a[index] if index < len(a) else None, b[index] if index < len(b) else None,
                     identity, f"{path}[{index}]")
        else:
            changes.append((identity, path, a, b))

    for family in ("pvms", "items"):
        old = {item["id"]: item for item in before.get(family, [])}
        new = {item["id"]: item for item in after.get(family, [])}
        for identity in sorted(old.keys() | new.keys()):
            walk(old.get(identity), new.get(identity), identity,
                 "Added" if identity not in old else "Removed" if identity not in new else "")
    walk({k: v for k, v in before.items() if k not in ("pvms", "items")},
         {k: v for k, v in after.items() if k not in ("pvms", "items")}, "Display", "")
    return changes


def describe_change(value):
    if value is None:
        return "—"
    if isinstance(value, dict) and ("kind" in value or "class" in value):
        label = value.get("label") or value.get("text") or value.get("id", "")
        return f"{value.get('kind') or value.get('class')} · {label} · ({value.get('x', 0):g}, {value.get('y', 0):g})"
    return str(value)


class RevisionReview(EngineeringDialog):
    def __init__(self, studio):
        super().__init__(studio, "Visual revision review")
        self.resize(1200, 780)
        self.views = []
        row = QHBoxLayout()
        self.before = AuthoringComboBox()
        self.after = AuthoringComboBox()
        for box in (self.before, self.after):
            box.addItem("Current draft", None)
            for entry in reversed(studio.store.history(studio.display.name)):
                box.addItem(f"Revision {entry['rev']} · {entry.get('env', '')}", entry["rev"])
        if self.before.count() > 1:
            self.before.setCurrentIndex(1)
        else:
            self.before.addItem("Empty display", -1)
            self.before.setCurrentIndex(1)
        for label, box in (("Before", self.before), ("After", self.after)):
            row.addWidget(QLabel(label))
            row.addWidget(box, 1)
            box.currentIndexChanged.connect(lambda *_: self.run(self.reload))
        self.button(row, "Refresh", self.reload)
        self.root.addLayout(row)
        self.split = QSplitter(Qt.Horizontal)
        self.root.addWidget(self.split, 2)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Object", "Property / change", "Before", "After"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.focus_change)
        self.root.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.button(bottom, "Locate on canvas", self.locate)
        self.button(bottom, "Fit both", self.fit_both)
        self.publish_button = self.button(
            bottom, "Continue to publish…", self.publish, primary=True)
        self.review_parent = None
        self.root.addLayout(bottom)
        self.run(self.reload)

    def document(self, box):
        revision = box.currentData()
        if revision is None:
            return self.studio._document()
        if revision == -1:
            return {"display": self.studio.display.name, "pvms": [], "items": []}
        result = self.studio.store.revision_document(self.studio.display.name, revision)
        if result is None:
            raise ValueError("That revision is unavailable")
        return result

    def reload(self):
        before, after = self.document(self.before), self.document(self.after)
        for view in self.views:
            view.close()
            view.setParent(None)
            view.deleteLater()
        self.views = []
        self.changes = semantic_changes(before, after)
        ids = {row[0] for row in self.changes}
        for document in (before, after):
            view = PvmDisplayView(document, self.studio.graphs_provider,
                                  config_root=self.studio.store.root, live=False,
                                  write_handler=lambda *_: False, write_checker=lambda *_: False)
            view.setInteractive(False)
            self.split.addWidget(view)
            self.views.append(view)
            for item in list(view.scene().items()):
                identity = getattr(getattr(item, "pvm", None), "id", "") or item_document_data(item).get("id")
                if identity in ids:
                    pen = QPen(QColor("#0078D4"), 2, Qt.DashLine)
                    pen.setCosmetic(True)
                    mark = view.scene().addRect(item.sceneBoundingRect(), pen)
                    mark.setZValue(100000)
        self.table.setRowCount(len(self.changes))
        for row, change in enumerate(self.changes):
            for col, value in enumerate(change):
                cell = QTableWidgetItem(describe_change(value))
                cell.setToolTip(str(value) if value is not None else "—")
                self.table.setItem(row, col, cell)
        self.status.setText(f"{len(self.changes)} changes. Blue outlines mark changed objects. Both views use current class definitions and current process values; this is a configuration comparison.")
        self.fit_both()

    def fit_both(self):
        rect = QRectF()
        for view in self.views:
            rect = rect.united(view.scene().itemsBoundingRect())
        for view in self.views:
            if not rect.isEmpty():
                view.fitInView(rect.adjusted(-15, -15, 15, 15), Qt.KeepAspectRatio)

    def focus_change(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.changes):
            return
        identity = self.changes[row][0]
        for view in self.views:
            for item in view.scene().items():
                if (getattr(getattr(item, "pvm", None), "id", "") or item_document_data(item).get("id")) == identity:
                    view.centerOn(item)
                    break

    def locate(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.changes):
            identity = self.changes[row][0]
            items = [item for item in self.studio._document_items() if self.studio._endpoint_id(item) == identity]
            if items:
                self.studio.selection.replace(items)
                self.studio.canvas.centerOn(items[0])
                self.hide()
            else:
                self.status.setText("This change has no object in the current canvas (removed object or display metadata).")

    def publish(self):
        if self.review_parent is not None:
            self.close()
            self.review_parent.raise_()
            return
        self.hide()
        self.studio.open_publish()

    def closeEvent(self, event):  # noqa: N802
        for view in self.views:
            view.close()
        super().closeEvent(event)
