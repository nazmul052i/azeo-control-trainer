"""Native Qt editing semantics with shared, bounded engineering popups."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFormLayout, QFrame, QLabel, QListView, QSizePolicy
from .brand import UI
from .dialog_layout import bounded_window_rect


class AuthoringComboBox(QComboBox):
    """Keep Qt's keyboard and accessibility behavior with an authored popup view."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup_view = QListView(self)
        self._popup_view.setFrameShape(QFrame.NoFrame)
        self._popup_view.setUniformItemSizes(True)
        self._popup_view.setTextElideMode(Qt.ElideRight)
        self._popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._popup_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setView(self._popup_view)
        self.setMaxVisibleItems(10)
        self.setMinimumContentsLength(10)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.currentTextChanged.connect(self.setToolTip)

    def showPopup(self):  # noqa: N802
        # The default Windows combo container can restore its native sunken frame.
        popup = self.view().parentWidget()
        popup.setObjectName("authoringPopup")
        popup.setFrameShape(QFrame.NoFrame)
        # A popup is a separate native surface. Explicitly paint its background;
        # rounded transparent corners otherwise expose a black Windows surface.
        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        if bind_operator_theme(popup) is None:
            popup.setStyleSheet(f"QFrame#authoringPopup {{background:{UI.pane}; border:1px solid {UI.field_border};}}")
        available = self.screen().availableGeometry()
        desired = max(self.width(), min(500, max(
            (self.fontMetrics().horizontalAdvance(self.itemText(i)) + 40
             for i in range(self.count())), default=180)))
        width = min(desired, max(1, available.width() - 16))
        self.view().setFixedWidth(width)
        popup.setMaximumWidth(width + 10)
        super().showPopup()
        popup.move(bounded_window_rect(popup.frameGeometry(), available).topLeft())


def name_form_fields(root):
    """Expose the existing visible labels to assistive tools and keyboard users."""
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            field_item = form.itemAt(row, QFormLayout.FieldRole)
            label_item = form.itemAt(row, QFormLayout.LabelRole)
            field = field_item.widget() if field_item else None
            label = label_item.widget() if label_item else None
            if field is not None and isinstance(label, QLabel):
                label.setBuddy(field)
                if not field.accessibleName():
                    field.setAccessibleName(label.text().replace("&", "").rstrip(":"))
