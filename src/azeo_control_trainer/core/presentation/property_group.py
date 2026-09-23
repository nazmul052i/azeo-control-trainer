"""Shared collapsible parameter group, promoted from Control Designer."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QToolButton, QVBoxLayout, QWidget
from .brand import UI


class PropertyGroup(QWidget):
    """A collapsible group box with a toggle button."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header button
        self._btn = QToolButton()
        self._btn.setText(f"  \u25BC  {title}")
        self._btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._btn.setCheckable(True)
        self._btn.setChecked(True)
        self._btn.clicked.connect(self._toggle)
        self._btn.setStyleSheet(
            f"QToolButton {{ background: {UI.chrome}; color: {UI.blue}; "
            f"border: 1px solid {UI.border}; border-radius: 2px; "
            "font-size: 9pt; font-weight: bold; "
            "padding: 3px 4px; }"
            f"QToolButton:hover {{ background: {UI.hover}; }}"
        )
        self._btn.setSizePolicy(self._btn.sizePolicy().horizontalPolicy(),
                                self._btn.sizePolicy().verticalPolicy())
        layout.addWidget(self._btn)

        # Content
        self._content = QWidget()
        self._form = QFormLayout(self._content)
        self._form.setContentsMargins(8, 4, 4, 4)
        self._form.setSpacing(3)
        self._form.setLabelAlignment(Qt.AlignRight)
        layout.addWidget(self._content)

    def form(self) -> QFormLayout:
        return self._form

    def is_collapsed(self) -> bool:
        return not self._btn.isChecked()

    def set_collapsed(self, collapsed: bool):
        self._btn.setChecked(not collapsed)
        self._content.setVisible(not collapsed)
        arrow = "\u25B6" if collapsed else "\u25BC"
        title = self._btn.text().lstrip(" \u25BC\u25B6 ")
        self._btn.setText(f"  {arrow}  {title}")

    def _toggle(self):
        visible = self._btn.isChecked()
        self._content.setVisible(visible)
        title = self._btn.text().lstrip(" \u25BC\u25B6 ")
        arrow = "\u25BC" if visible else "\u25B6"
        self._btn.setText(f"  {arrow}  {title}")
