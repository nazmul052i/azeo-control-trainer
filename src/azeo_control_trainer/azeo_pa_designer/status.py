"""Compact editor status, driven by the document and canvas."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QStatusBar

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.ribbon import SmallRibbonButton


class ProcedureStatusBar(QStatusBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QStatusBar {{ background: {UI.chrome}; border-top: 1px solid {UI.border}; }}
            QStatusBar::item {{ border: none; }}
            QStatusBar QLabel {{ padding: 0 8px; color: {UI.text_secondary}; }}
        """)
        self.document = QLabel("Not saved")
        self.steps = QLabel("0 blocks")
        self.selection = QLabel("No selection")
        self.selection.setTextFormat(Qt.PlainText)
        self.selection.setFixedWidth(125)
        for label in (self.document, self.steps, self.selection):
            self.addPermanentWidget(label)
        self.zoom = SmallRibbonButton("100%", "restore", "Reset workflow zoom to 100%")
        self.zoom.setAccessibleName("Reset workflow zoom")
        self.addPermanentWidget(self.zoom)
        self.fit = SmallRibbonButton("Fit", "zoom_fit", "Fit the whole procedure in the canvas")
        self.addPermanentWidget(self.fit)

    def setText(self, message):  # noqa: N802
        # QStatusBar clips long messages before the permanent segments. A loose
        # word-wrapped label used to grow the window when reporting a long path.
        self.showMessage(message)
        self.setToolTip(message)

    def text(self):
        return self.currentMessage()

    def set_zoom(self, scale):
        self.zoom.setText(f"{scale * 100:.0f}%")

    def set_selection(self, step_ids):
        text = step_ids[0] if len(step_ids) == 1 else f"{len(step_ids)} selected" if step_ids else "No selection"
        metrics = self.selection.fontMetrics()
        width = max(125, metrics.horizontalAdvance("999 selected") + 16)
        self.selection.setFixedWidth(width)
        self.selection.setText(metrics.elidedText(text, Qt.ElideRight, width - 16))
        self.selection.setToolTip("\n".join(step_ids) if step_ids else text)
