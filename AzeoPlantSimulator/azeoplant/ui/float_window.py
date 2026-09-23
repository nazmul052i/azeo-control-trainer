"""Shared shell for the floating tool windows.

Every teaching window gets the same anatomy as the operator display:
the title band, themed chrome, a consistent size, and a refresh that
forwards to the content. The content widget keeps its own painting
and timers - the shell is pure chrome, so the windows finally read as
one product family.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .hmi import BAND, TEXT


class FloatingShell(QWidget):
    def __init__(self, title: str, content: QWidget, subtitle: str = "",
                 size=(760, 560)) -> None:
        super().__init__(None, Qt.Window)
        self.content = content
        self.setStyleSheet(theme.STYLESHEET)
        self.setWindowTitle(f"{title}  ·  AzeoPlant")

        header = QWidget()
        header.setFixedHeight(30)
        header.setStyleSheet(
            f"background: {BAND.name()};"
            "border-bottom: 1px solid #C2C6CB;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 0, 12, 0)
        t = QLabel(title.upper())
        t.setFont(theme.font(11, bold=True))
        t.setStyleSheet(
            f"color: {TEXT.name()}; background: transparent;")
        hl.addWidget(t)
        hl.addStretch(1)
        if subtitle:
            s = QLabel(subtitle)
            s.setFont(theme.font(8))
            s.setStyleSheet(
                f"color: {theme.MUTED_TEXT.name()};"
                " background: transparent;")
            hl.addWidget(s)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(content, 1)
        self.resize(*size)

    def refresh(self, snap) -> None:
        fn = getattr(self.content, "refresh", None)
        if fn is not None:
            fn(snap)
        else:
            self.content.update()
