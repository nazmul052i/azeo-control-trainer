"""Wrap command rows so tools remain usable in a narrow operating pane."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QFrame, QLayout, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(5)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def addWidget(self, widget, stretch=0, alignment=None):  # noqa: N802
        # A wrapping command row sizes each field from its hint; stretch is
        # accepted when an existing form switches from a one-line layout.
        super().addWidget(widget)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._place(QRect(0, 0, width, 0), measure=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._place(rect)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _place(self, rect, measure=False):
        area = rect.marginsRemoved(self.contentsMargins())
        x, y, height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            width = min(size.width(), area.width())
            if x + width > area.right() + 1 and height:
                x, y, height = area.x(), y + height + self.spacing(), 0
            if not measure:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, size.height())))
            x += width + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y() + self.contentsMargins().bottom()


class FlowBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self)

    def addWidget(self, widget):  # noqa: N802
        self.flow.addWidget(widget)

    def addSeparator(self):  # noqa: N802
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedHeight(22)
        self.flow.addWidget(separator)
