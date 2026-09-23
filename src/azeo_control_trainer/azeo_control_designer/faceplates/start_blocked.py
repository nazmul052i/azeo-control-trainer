"""The dialog that answers "why won't it start?".

The interlock spec is explicit (§27) that *Start failed* is not an acceptable
message: name the conditions and say what has to change. Every line here comes
from the block's own configured channel descriptions, so the wording an
engineer puts in the module is the wording the operator reads.

Shared by the motor and device faceplates — a refused start is the same
conversation whichever block refused it.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from azeo_control_trainer.core.pid.theme.colors import BG_INSET
from azeo_control_trainer.core.pid.widgets.io_faceplates import _make_header
from azeo_control_trainer.core.presentation.headless import is_headless

from .faceplate_widgets import BAD, BODY_BG, BORDER, TAG_COLOR, VALUE_COLOR

log = logging.getLogger("ui.faceplates.start_blocked")


class StartBlockedDialog(QDialog):
    """Why a start command was refused, and what to do about it."""

    def __init__(self, tag: str, reasons: list[tuple[str, str]],
                 guidance: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{tag} — start blocked")
        self.setStyleSheet(f"QDialog {{ background-color: {BODY_BG}; }}")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        root.addWidget(_make_header(tag, "START BLOCKED"))

        body = QVBoxLayout()
        body.setContentsMargins(8, 2, 8, 2)
        body.setSpacing(3)
        cap = QLabel("Blocked by:" if reasons
                     else "No blocking condition is currently reported.")
        cap.setStyleSheet(
            f"QLabel {{ font-size: 8pt; font-weight: bold; color: {TAG_COLOR}; }}")
        body.addWidget(cap)
        for label, desc in reasons:
            row = QLabel(f"✗  {label}   {desc}")
            row.setWordWrap(True)
            row.setStyleSheet(
                f"QLabel {{ font-size: 9pt; color: {BAD};"
                " font-family: Consolas; }")
            body.addWidget(row)
        if guidance:
            note = QLabel(guidance)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"QLabel {{ font-size: 8pt; color: {VALUE_COLOR};"
                f" background-color: {BG_INSET}; border: 1px solid {BORDER};"
                " border-radius: 3px; padding: 5px; }")
            body.addWidget(note)
        root.addLayout(body)

        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(72, 26)
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)


def show_start_blocked(tag: str, reasons: list[tuple[str, str]],
                       parent=None) -> StartBlockedDialog | None:
    """Explain a refused start — modally, unless nobody is there to answer.

    A faceplate button is direct user action, so this is not the class of
    prompt that fires by itself. It still has to be guarded: under the
    offscreen platform a UI test that presses START would sit on ``exec()``
    until it was killed. Headless the refusal goes to the log instead, which
    is also where a test can assert on it.
    """
    if is_headless():
        log.info("%s: start blocked — %s", tag,
                 "; ".join(f"{label} ({desc})" for label, desc in reasons)
                 or "no reported cause")
        return None
    dlg = StartBlockedDialog(tag, reasons, guidance_for(reasons, tag), parent)
    dlg.exec()
    return dlg


def guidance_for(reasons: list[tuple[str, str]], tag: str) -> str:
    """One sentence telling the operator what to fix, built from the causes."""
    if not reasons:
        return ""
    items = [d for _, d in reasons if d]
    if not items:
        return f"Clear the conditions above before starting {tag}."
    if len(items) == 1:
        return f"Satisfy “{items[0]}” before starting {tag}."
    joined = ", ".join(items[:-1]) + f" and {items[-1]}"
    return f"Satisfy {joined} before starting {tag}."
