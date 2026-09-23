"""Consistent dialog chrome for the Azeo engineering applications.

The main authoring windows already share the Control Designer palette, but a
dialog does not automatically inherit that visual hierarchy when it is opened
as a native top-level window.  This module supplies the small amount of chrome
that distinguishes an engineering workflow from an unstyled Qt form.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .authoring_style import AUTHORING_CHROME_QSS
from .brand import UI


AUTHORING_DIALOG_QSS = AUTHORING_CHROME_QSS + f"""
QDialog[authoringDialog="true"] {{ background: {UI.page}; }}
QDialog[authoringDialog="true"] QScrollArea > QWidget > QWidget {{
    background: {UI.page};
}}
QFrame#authoring_dialog_header {{
    background: {UI.blue}; border: none; border-radius: 5px;
}}
QLabel#authoring_dialog_kicker {{
    color: #CFE3F5; background: transparent; border: none;
    font-size: 8.5pt; font-weight: 600;
}}
QLabel#authoring_dialog_title {{
    color: white; background: transparent; border: none;
    font-size: 13pt; font-weight: 650;
}}
QLabel#authoring_dialog_description {{
    color: #E7F0F8; background: transparent; border: none;
    font-size: 9.5pt;
}}
QDialog[authoringDialog="true"] QGroupBox {{
    background: {UI.pane}; color: {UI.blue};
    border: 1px solid {UI.border}; border-radius: 5px;
    margin-top: 10px; padding: 12px 9px 9px 9px; font-weight: 600;
}}
QDialog[authoringDialog="true"] QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 9px; padding: 0 4px; color: {UI.blue};
}}
QDialogButtonBox#authoring_dialog_buttons {{
    background: {UI.chrome}; border-top: 1px solid {UI.border};
    padding-top: 8px;
}}
QPushButton[primaryAction="true"] {{
    color: {UI.on_blue}; background: {UI.blue}; border-color: {UI.blue};
    font-weight: 600; min-width: 76px;
}}
QPushButton[primaryAction="true"]:hover {{
    color: {UI.on_blue}; background: {UI.menu_hover};
    border-color: {UI.menu_hover};
}}
QPushButton[primaryAction="true"]:pressed {{
    color: {UI.on_blue}; background: {UI.menu_pressed};
    border-color: {UI.menu_pressed};
}}
QPushButton[primaryAction="true"]:disabled {{
    color: #EEF2F6; background: {UI.disabled}; border-color: {UI.disabled};
}}
"""


def apply_authoring_dialog(dialog: QDialog) -> None:
    """Apply the authoring palette without discarding local dialog rules."""
    dialog.setProperty("authoringDialog", True)
    existing = dialog.styleSheet()
    if "authoring_dialog_header" not in existing:
        dialog.setStyleSheet(AUTHORING_DIALOG_QSS + "\n" + existing)


def apply_compact_authoring_dialog(dialog: QDialog) -> None:
    """Style a small picker/tool window that intentionally starts at content."""
    apply_authoring_dialog(dialog)
    dialog.setProperty("compactAuthoringDialog", True)


def add_authoring_dialog_header(
    dialog: QDialog,
    layout,
    title: str,
    description: str = "",
    *,
    product: str = "AZEO GRAPHICS DESIGNER",
) -> QFrame:
    """Insert the shared branded header at the start of a dialog layout."""
    apply_authoring_dialog(dialog)
    header = QFrame(dialog)
    header.setObjectName("authoring_dialog_header")
    content = QVBoxLayout(header)
    content.setContentsMargins(16, 11, 16, 12)
    content.setSpacing(2)

    kicker = QLabel(product, header)
    kicker.setObjectName("authoring_dialog_kicker")
    content.addWidget(kicker)

    heading = QLabel(title, header)
    heading.setObjectName("authoring_dialog_title")
    heading.setWordWrap(True)
    content.addWidget(heading)

    if description:
        summary = QLabel(description, header)
        summary.setObjectName("authoring_dialog_description")
        summary.setWordWrap(True)
        content.addWidget(summary)

    layout.insertWidget(0, header)
    return header


def mark_primary_action(button: QPushButton | None) -> None:
    """Give one affirmative action the same visual priority in every dialog."""
    if button is None:
        return
    button.setProperty("primaryAction", True)
    button.style().unpolish(button)
    button.style().polish(button)


def style_dialog_buttons(
    buttons: QDialogButtonBox,
    standard_button: QDialogButtonBox.StandardButton | None = None,
) -> None:
    """Style a button row and identify its affirmative action."""
    buttons.setObjectName("authoring_dialog_buttons")
    if standard_button is not None:
        mark_primary_action(buttons.button(standard_button))
        return
    for role in (
        QDialogButtonBox.AcceptRole,
        QDialogButtonBox.ApplyRole,
        QDialogButtonBox.YesRole,
    ):
        candidates = [button for button in buttons.buttons()
                      if buttons.buttonRole(button) == role]
        if candidates:
            mark_primary_action(candidates[0])
            return


__all__ = [
    "AUTHORING_DIALOG_QSS",
    "add_authoring_dialog_header",
    "apply_authoring_dialog",
    "apply_compact_authoring_dialog",
    "mark_primary_action",
    "style_dialog_buttons",
]
