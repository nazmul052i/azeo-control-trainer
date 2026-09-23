"""Tag browser / picker widget for selecting process tags in IO block config.

Shows all available tags from TAG_REGISTRY organized by category in a
searchable tree dialog. Used by the properties panel when configuring
AI/AO/DI/DO blocks.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.tag_registry import TAG_REGISTRY, _CATEGORY_ORDER


def _get_tags_by_category() -> dict[str, list[str]]:
    """Return tags grouped by category, in display order."""
    by_cat: dict[str, list[str]] = {}
    for tag, meta in TAG_REGISTRY.items():
        by_cat.setdefault(meta.category, []).append(tag)
    # Sort tags within each category
    for cat in by_cat:
        by_cat[cat].sort()
    return by_cat


class TagBrowserDialog(QDialog):
    """Modal dialog for browsing and selecting a process tag."""

    def __init__(self, current_tag: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browse Tags")
        self.setMinimumSize(480, 500)
        self.selected_tag = current_tag

        self._build_ui()
        self._populate()

        if current_tag:
            self._select_tag(current_tag)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter tags...")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search)
        self.shared_catalog_button = QPushButton("Shared configuration…")
        self.shared_catalog_button.clicked.connect(self._browse_configuration)
        search_row.addWidget(self.shared_catalog_button)
        layout.addLayout(search_row)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Tag", "Description", "Units", "Range"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self._tree, 1)

        # Info label
        self._info = QLabel("")
        self._info.setStyleSheet(f"color: {UI.text_secondary}; font-size: 9pt; padding: 2px;")
        layout.addWidget(self._info)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("Select")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        # Styling
        self.setStyleSheet(f"""
            QDialog {{ background: #fafafa; }}
            QTreeWidget {{
                background: #ffffff; color: #333;
                border: 1px solid {UI.border};
                alternate-background-color: #f5f5f5;
                font-size: 9pt;
            }}
            QTreeWidget::item:selected {{ background: {UI.blue}; color: #fff; }}
            QTreeWidget::item:hover {{ background: #D2DFEF; }}
            QHeaderView::section {{
                background: {UI.border_light}; color: {UI.text_secondary};
                border: 1px solid {UI.border}; padding: 3px; font-size: 9pt;
            }}
            QLineEdit {{
                background: #ffffff; border: 1px solid {UI.border};
                border-radius: 2px; padding: 4px; font-size: 9pt;
            }}
            QPushButton {{
                background: #ffffff; color: {UI.text_secondary};
                border: 1px solid {UI.border}; border-radius: 3px;
                padding: 5px 16px; font-size: 9pt;
            }}
            QPushButton:hover {{ background: #D2DFEF; border-color: #64b5f6; }}
            QPushButton:pressed {{ background: #B8CCE4; }}
            QLabel {{ color: {UI.text_secondary}; font-size: 9pt; }}
        """)

    def _populate(self):
        by_cat = _get_tags_by_category()
        for cat in _CATEGORY_ORDER:
            tags = by_cat.get(cat)
            if not tags:
                continue
            cat_item = QTreeWidgetItem(self._tree, [cat])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsSelectable)
            font = QFont(self._tree.font())
            font.setBold(True)
            cat_item.setFont(0, font)
            cat_item.setExpanded(False)

            for tag in tags:
                meta = TAG_REGISTRY[tag]
                range_str = f"{meta.eng_min}..{meta.eng_max}"
                desc = meta.description or meta.display_name
                child = QTreeWidgetItem(cat_item, [
                    meta.display_name, desc, meta.display_unit, range_str
                ])
                child.setData(0, Qt.UserRole, tag)

        # Remaining categories
        for cat, tags in by_cat.items():
            if cat in _CATEGORY_ORDER:
                continue
            cat_item = QTreeWidgetItem(self._tree, [cat])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsSelectable)
            font = QFont(self._tree.font())
            font.setBold(True)
            cat_item.setFont(0, font)
            for tag in tags:
                meta = TAG_REGISTRY[tag]
                range_str = f"{meta.eng_min}..{meta.eng_max}"
                desc = meta.description or meta.display_name
                child = QTreeWidgetItem(cat_item, [
                    meta.display_name, desc, meta.display_unit, range_str
                ])
                child.setData(0, Qt.UserRole, tag)

        self._info.setText(f"{len(TAG_REGISTRY)} tags available")

    def _filter(self, text: str):
        text = text.lower()
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            visible_children = 0
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                tag = child.data(0, Qt.UserRole) or ""
                desc = child.text(1)
                match = text in tag.lower() or text in desc.lower()
                child.setHidden(not match)
                if match:
                    visible_children += 1
            cat_item.setHidden(visible_children == 0)
            if visible_children > 0 and text:
                cat_item.setExpanded(True)

    def _select_tag(self, tag: str):
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                if child.data(0, Qt.UserRole) == tag:
                    cat_item.setExpanded(True)
                    self._tree.setCurrentItem(child)
                    self._tree.scrollToItem(child)
                    return

    def _browse_configuration(self):
        from azeo_control_trainer.core.presentation.configuration_catalog import (
            ConfigurationCatalogDialog, context_root,
        )
        dlg = ConfigurationCatalogDialog(context_root(self), self, pick="field")
        self._configuration_picker = dlg
        def accept_tag():
            if dlg.selected_tag:
                self.selected_tag = dlg.selected_tag["io_tag"]
                self.accept()
        dlg.accepted.connect(accept_tag)
        dlg.show()

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        tag = item.data(0, Qt.UserRole)
        if tag:
            self.selected_tag = tag
            self.accept()

    def _accept(self):
        item = self._tree.currentItem()
        if item:
            tag = item.data(0, Qt.UserRole)
            if tag:
                self.selected_tag = tag
                self.accept()


class TagPickerWidget(QWidget):
    """Inline widget with a text field and browse button for tag selection."""

    tagChanged = Signal(str)

    def __init__(self, current_tag: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._edit = QLineEdit(current_tag)
        self._edit.setPlaceholderText("e.g. COT, ctrl.TIC101.PV")
        self._edit.editingFinished.connect(self._on_edited)
        layout.addWidget(self._edit, 1)

        self._btn = QPushButton("...")
        self._btn.setFixedWidth(28)
        self._btn.setToolTip("Browse tags")
        self._btn.clicked.connect(self._browse)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: #ffffff; color: {UI.text_secondary};
                border: 1px solid {UI.border}; border-radius: 2px;
                padding: 2px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #D2DFEF; border-color: #64b5f6; }}
        """)
        layout.addWidget(self._btn)

    def tag(self) -> str:
        return self._edit.text()

    def set_tag(self, tag: str):
        self._edit.setText(tag)

    def _on_edited(self):
        self.tagChanged.emit(self._edit.text())

    def _browse(self):
        dlg = TagBrowserDialog(self._edit.text(), self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        def _on_accepted():
            if dlg.selected_tag:
                self._edit.setText(dlg.selected_tag)
                self.tagChanged.emit(dlg.selected_tag)
        dlg.accepted.connect(_on_accepted)
        dlg.show()
