# -*- coding: utf-8 -*-
"""The quick navigation palette (Ctrl+K).

Type a few characters of a tag, a module or a description and go: Enter
opens the faceplate, Ctrl+Enter adds the point to the trend. The palette
searches every tag (name, description, unit) and every control module,
prefix matches first.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from . import theme

MAX_ROWS = 50


class QuickNav(QDialog):
    def __init__(self, db, controller,
                 open_ref: Callable[[str], None],
                 add_pen: Callable[[str], None], parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.controller = controller
        self.open_ref = open_ref
        self.add_pen = add_pen
        self.setWindowTitle("Go to...")
        self.setModal(True)
        self.resize(520, 420)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("tag, module or description")
        self.edit.setFont(theme.font(11))
        self.edit.textChanged.connect(self._filter)

        self.list = QListWidget()
        self.list.setFont(theme.font(9, mono=True))
        self.list.itemActivated.connect(lambda _i: self._go(False))

        hint = QLabel("Enter opens the faceplate      "
                      "Ctrl+Enter adds to the trend      Esc closes")
        hint.setFont(theme.font(8))
        hint.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")

        lay = QVBoxLayout(self)
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        lay.addWidget(hint)

        QShortcut(QKeySequence("Ctrl+Return"), self,
                  lambda: self._go(True))
        QShortcut(QKeySequence("Ctrl+Enter"), self,
                  lambda: self._go(True))

        # (search key, display row, ref, trendable tag)
        self._index: List[Tuple[str, str, str, str]] = []
        self._build_index()
        self._filter("")
        self.edit.setFocus()

    # ------------------------------------------------------------------ index
    def _build_index(self) -> None:
        rows = []
        if self.controller is not None:
            for module, loop in self.controller.loops.items():
                desc = getattr(loop.pid, "description", "") or ""
                rows.append((f"{module} {desc}".upper(),
                             f"{module:<12} {desc}  [loop]",
                             module, loop.pv_tag))
        for t in self.db.all():
            rows.append((f"{t.name} {t.desc}".upper(),
                         f"{t.name:<12} {t.desc}  ({t.unit} {t.kind})",
                         t.name, t.name))
        self._index = rows

    def _filter(self, _text: str = "") -> None:
        needle = self.edit.text().strip().upper()
        self.list.clear()
        starts, contains = [], []
        for key, row, ref, tag in self._index:
            if not needle:
                contains.append((row, ref, tag))
            elif key.startswith(needle):
                starts.append((row, ref, tag))
            elif needle in key:
                contains.append((row, ref, tag))
            if len(starts) >= MAX_ROWS:
                break
        for row, ref, tag in (starts + contains)[:MAX_ROWS]:
            item = QListWidgetItem(row)
            item.setData(Qt.UserRole, (ref, tag))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    # ---------------------------------------------------------------- actions
    def _current(self) -> Optional[Tuple[str, str]]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _go(self, to_trend: bool) -> None:
        cur = self._current()
        if cur is None:
            return
        ref, tag = cur
        self.accept()
        if to_trend:
            self.add_pen(tag)
        else:
            self.open_ref(ref)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        # the edit keeps focus; arrows and enter drive the list
        if ev.key() in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown,
                        Qt.Key_PageUp):
            self.list.keyPressEvent(ev)
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._go(bool(ev.modifiers() & Qt.ControlModifier))
            return
        super().keyPressEvent(ev)
