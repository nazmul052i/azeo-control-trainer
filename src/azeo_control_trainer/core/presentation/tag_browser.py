"""Categorized tag browser with search filter.

Shows a QTreeWidget with categories as top-level items and tags as
children.  Double-click a tag to add it to the active trend pane.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .tag_registry import TagMeta


# Display order for categories
_CATEGORY_ORDER = [
    'Temperatures', 'Flows', 'Pressure', 'Combustion',
    'Heat Duty', 'Valves', 'Controllers', 'Simulation', 'Other',
]


class TagBrowser(QWidget):
    """Categorized tag tree with search filter."""

    tag_selected = Signal(str)  # emitted on double-click

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_tags: Dict[str, TagMeta] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Header
        hdr = QLabel("Tag Browser")
        hdr.setStyleSheet("font-weight: bold; font-size: 10pt; padding: 4px;")
        lay.addWidget(hdr)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter tags...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        lay.addWidget(self._search)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Tag", "Unit"])
        self._tree.setColumnWidth(0, 150)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self._tree, 1)

        # Tag count
        self._lbl_count = QLabel("0 tags")
        self._lbl_count.setStyleSheet("color: #808080; font-size: 8pt;")
        lay.addWidget(self._lbl_count)

    def populate(self, registry: Dict[str, TagMeta]):
        """Build the tree from the tag registry."""
        self._all_tags = registry
        self._tree.clear()

        # Group by category, then for Controllers group by controller tag
        grouped: Dict[str, list] = defaultdict(list)
        ctrl_grouped: Dict[str, list] = defaultdict(list)

        for tag, meta in sorted(registry.items(), key=lambda kv: kv[0]):
            if meta.category == 'Controllers':
                # Extract controller name: ctrl.TIC101.PV -> TIC101
                parts = tag.split('.')
                if len(parts) >= 3:
                    ctrl_name = parts[1]
                    ctrl_grouped[ctrl_name].append(meta)
                else:
                    grouped['Controllers'].append(meta)
            else:
                grouped[meta.category].append(meta)

        # Build tree
        for cat in _CATEGORY_ORDER:
            if cat == 'Controllers' and ctrl_grouped:
                cat_item = QTreeWidgetItem(self._tree, [cat, ""])
                cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsSelectable)
                cat_item.setExpanded(False)
                # Sub-group by controller
                for ctrl_name in sorted(ctrl_grouped.keys()):
                    ctrl_item = QTreeWidgetItem(cat_item, [ctrl_name, ""])
                    ctrl_item.setFlags(ctrl_item.flags() & ~Qt.ItemIsSelectable)
                    for meta in ctrl_grouped[ctrl_name]:
                        suffix = meta.tag.split('.')[-1]  # PV, SP, OUT
                        child = QTreeWidgetItem(ctrl_item, [suffix, meta.display_unit])
                        child.setData(0, Qt.UserRole, meta.tag)
                        child.setToolTip(0, f"{meta.tag} -- {meta.display_name}")
            elif cat in grouped:
                cat_item = QTreeWidgetItem(self._tree, [cat, ""])
                cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsSelectable)
                cat_item.setExpanded(cat == 'Temperatures')
                for meta in grouped[cat]:
                    child = QTreeWidgetItem(cat_item, [meta.display_name, meta.display_unit])
                    child.setData(0, Qt.UserRole, meta.tag)
                    tip = meta.description or meta.tag
                    child.setToolTip(0, f"{meta.tag} -- {tip}")

        self._lbl_count.setText(f"{len(registry)} tags")

    def _on_search(self, text: str):
        """Filter tree items by text match."""
        text_lower = text.lower()
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            cat_visible = False

            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                # Check if child is a controller group (has children)
                if child.childCount() > 0:
                    group_visible = False
                    for k in range(child.childCount()):
                        leaf = child.child(k)
                        tag = leaf.data(0, Qt.UserRole) or ""
                        match = (text_lower in tag.lower()
                                 or text_lower in leaf.text(0).lower()
                                 or text_lower in child.text(0).lower())
                        leaf.setHidden(not match)
                        if match:
                            group_visible = True
                    child.setHidden(not group_visible)
                    if group_visible:
                        cat_visible = True
                else:
                    tag = child.data(0, Qt.UserRole) or ""
                    match = (text_lower in tag.lower()
                             or text_lower in child.text(0).lower())
                    child.setHidden(not match)
                    if match:
                        cat_visible = True

            cat_item.setHidden(not cat_visible)
            if cat_visible and text:
                cat_item.setExpanded(True)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        tag = item.data(0, Qt.UserRole)
        if tag:
            self.tag_selected.emit(tag)
