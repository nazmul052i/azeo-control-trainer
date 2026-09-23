"""Large-diagram minimap, block navigator, and named canvas bookmarks."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import apply_compact_authoring_dialog


@dataclass(frozen=True)
class DiagramBookmark:
    name: str
    center: QPointF


class _MiniMapView(QGraphicsView):
    def __init__(self, scene, main_view, parent=None):
        super().__init__(scene, parent)
        self._main_view = main_view
        self.setInteractive(False)
        self.setMinimumHeight(170)
        self.setStyleSheet(f"border: 1px solid {UI.border}; background: #ECEFF1;")

    def frame_all(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        self._main_view.centerOn(self.mapToScene(event.position().toPoint()))
        event.accept()


class DiagramNavigatorDialog(QDialog):
    """Session navigator; bookmarks never modify executable strategy data."""

    def __init__(self, scene, main_view, parent=None):
        super().__init__(parent)
        self._scene = scene
        self._main_view = main_view
        self._bookmarks: list[DiagramBookmark] = []
        self.setWindowTitle("Diagram Navigator")
        apply_compact_authoring_dialog(self)
        self.setModal(False)
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        title = QLabel("DIAGRAM NAVIGATOR")
        title.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        layout.addWidget(title)
        self.minimap = _MiniMapView(scene, main_view)
        layout.addWidget(self.minimap)

        split = QSplitter(Qt.Horizontal)
        self.blocks = QListWidget()
        self.blocks.itemActivated.connect(self._go_block)
        split.addWidget(self._wrap("Blocks", self.blocks))
        self.bookmarks = QListWidget()
        self.bookmarks.itemActivated.connect(self._go_bookmark)
        split.addWidget(self._wrap("Bookmarks", self.bookmarks))
        layout.addWidget(split, 1)

        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Bookmark name")
        add = QPushButton("Add Current View")
        remove = QPushButton("Remove")
        add.clicked.connect(self.add_current_bookmark)
        remove.clicked.connect(self.remove_current_bookmark)
        row.addWidget(self.name, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        self.refresh()

    @staticmethod
    def _wrap(title: str, child) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)
        layout.addWidget(child, 1)
        return widget

    def refresh(self):
        self.blocks.clear()
        for block in self._scene.graph.blocks.values():
            item = QListWidgetItem(f"{block.instance_name}  ·  {block.block_type}")
            item.setData(Qt.UserRole, block.id)
            self.blocks.addItem(item)
        self.minimap.frame_all()

    def add_current_bookmark(self, name: str = ""):
        name = (name or self.name.text()).strip()
        if not name:
            name = f"View {len(self._bookmarks) + 1}"
        center = self._main_view.mapToScene(self._main_view.viewport().rect().center())
        bookmark = DiagramBookmark(name, QPointF(center))
        self._bookmarks.append(bookmark)
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, bookmark)
        self.bookmarks.addItem(item)
        self.bookmarks.setCurrentItem(item)
        self.name.clear()

    def remove_current_bookmark(self):
        row = self.bookmarks.currentRow()
        if row < 0:
            return
        self.bookmarks.takeItem(row)
        del self._bookmarks[row]

    def _go_block(self, item):
        block_item = self._scene.get_block_item(item.data(Qt.UserRole))
        if block_item is None:
            return
        self._scene.clearSelection()
        block_item.setSelected(True)
        self._main_view.centerOn(block_item)

    def _go_bookmark(self, item):
        bookmark = item.data(Qt.UserRole)
        self._main_view.centerOn(bookmark.center)

    def step_block(self, delta: int):
        if not self.blocks.count():
            return
        row = self.blocks.currentRow()
        row = (row + delta) % self.blocks.count()
        self.blocks.setCurrentRow(row)
        self._go_block(self.blocks.item(row))
