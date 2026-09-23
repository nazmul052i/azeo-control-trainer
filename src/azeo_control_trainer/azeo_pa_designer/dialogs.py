"""PA Designer-owned prompts using the same engineering chrome as the editor."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.configuration_chrome import icon, style_button
from azeo_control_trainer.core.presentation.engineering_dialog import polish_dialog


class TrialInputsDialog(QDialog):
    """Collect bounded JSON inputs without falling back to a generic Qt prompt."""

    def __init__(self, defaults: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Isolated procedure trial")
        self.resize(780, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Preset logical tag values as JSON. Outputs remain in memory and "
            "are never connected to the controller.",
            wordWrap=True,
        ))
        self.editor = QPlainTextEdit(defaults)
        self.editor.setAccessibleName("Preset logical tag values as JSON")
        layout.addWidget(self.editor, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Run isolated trial")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        polish_dialog(
            self,
            title="Isolated procedure trial",
            subtitle="Review the logical inputs before running the offline procedure.",
            mark="simulator",
        )

    def text_value(self) -> str:
        return self.editor.toPlainText()


def unsaved_procedure_dialog(parent) -> QMessageBox:
    """Build the revision prompt so its visual contract can be tested."""
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Unsaved procedure")
    dialog.setText("Save a new revision before leaving this procedure?")
    dialog.setStandardButtons(
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
    )
    dialog.setDefaultButton(QMessageBox.Cancel)
    dialog.setIcon(QMessageBox.NoIcon)
    dialog.setIconPixmap(icon("save").pixmap(32, 32))
    polish_dialog(dialog)
    style_button(dialog.button(QMessageBox.Save), "save", primary=True)
    style_button(dialog.button(QMessageBox.Discard), "delete")
    style_button(dialog.button(QMessageBox.Cancel), "deactivate", quiet=True)
    return dialog


def export_procedure_dialog(parent, initial: str) -> QFileDialog:
    """Use the Qt picker so application styling is stable across workstations."""
    dialog = QFileDialog(
        parent,
        "Export procedure document",
        initial,
        "HTML document (*.html);;Markdown document (*.md)",
    )
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    dialog.setFileMode(QFileDialog.AnyFile)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setWindowIcon(icon("download"))
    polish_dialog(dialog)
    for button in dialog.findChildren(QPushButton):
        text = button.text().replace("&", "")
        if text in {"Save", "Open"}:
            style_button(button, "download", primary=True)
        elif text == "Cancel":
            style_button(button, "deactivate", quiet=True)
    return dialog


def get_export_procedure_path(parent, initial: str) -> str | None:
    dialog = export_procedure_dialog(parent, initial)
    if dialog.exec() != QDialog.Accepted:
        return None
    files = dialog.selectedFiles()
    return files[0] if files else None
