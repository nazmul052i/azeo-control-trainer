"""Search the commands and property editors that the active window owns."""
from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
    QStackedWidget, QTabWidget,
)

from azeo_control_trainer.core.presentation.authoring_dialog import (
    apply_compact_authoring_dialog,
)


def preferences():
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "Azeo", "GraphicsDesigner")


class SearchDialog(QDialog):
    def showEvent(self, event):  # noqa: N802
        from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
        fit_dialog_to_screen(self)
        super().showEvent(event)

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 480)
        apply_compact_authoring_dialog(self)
        # Command palettes deliberately start at the search field; a full
        # workflow header would reduce the result area without adding context.
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to search…")
        self.search.installEventFilter(self)
        root.addWidget(self.search)
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.activate)
        self.list.itemActivated.connect(self.activate)
        self.entries = []

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.search and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Down, Qt.Key_Up):
            delta = 1 if event.key() == Qt.Key_Down else -1
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + delta)))
            return True
        return super().eventFilter(watched, event)

    def filter(self, *_):
        query = self.search.text().casefold().split()
        self.list.clear()
        self._callbacks = []
        for label, callback in self.entries:
            if all(part in label.casefold() for part in query):
                row = QListWidgetItem(label)
                row.setData(Qt.UserRole, len(self._callbacks))
                self.list.addItem(row)
                self._callbacks.append(callback)
        self.list.setCurrentRow(0)

    def activate(self, *_):
        item = self.list.currentItem()
        if item is not None:
            callback = self._callbacks[item.data(Qt.UserRole)]
            self.close()
            callback()


class CommandSearch(SearchDialog):
    def __init__(self, window):
        super().__init__(window, "Find a command · Ctrl+K")
        seen = set()
        for tab, groups in window._RIBBON.items():
            for group, commands in groups:
                for _glyph, label, action, _checkable in commands:
                    if action in seen or action == "tools.command_search":
                        continue
                    seen.add(action)
                    self.entries.append((f"{label}  ·  {tab} / {group}", lambda a=action: window.dispatch(a)))
        for payload in recent_assets():
            label = payload.get("symbol") or payload.get("name") or payload.get("block_type") or payload.get("kind") or payload.get("type")
            self.entries.insert(0, (f"Recent component · {label}", lambda p=payload: window._arm_palette_item(p)))
        self.filter()


def recent_assets():
    try:
        return json.loads(preferences().value("recent_assets", "[]"))[:12]
    except (ValueError, TypeError):
        return []


def remember_asset(payload):
    values = [payload] + [one for one in recent_assets() if one != payload]
    preferences().setValue("recent_assets", json.dumps(values[:12]))


class PropertySearch(SearchDialog):
    def __init__(self, pane):
        super().__init__(pane, "Find a property")
        self.pane = pane
        self.settings = preferences()
        self.favorites = set(self.settings.value("favorite_properties", [], type=list))
        self.only_favorites = QCheckBox("Favorites only")
        self.layout().insertWidget(1, self.only_favorites)
        row = QHBoxLayout()
        star = QPushButton("Favorite / Unfavorite")
        star.clicked.connect(self.toggle_favorite)
        row.addWidget(star)
        jump = QPushButton("Go to property")
        jump.clicked.connect(self.activate)
        row.addWidget(jump)
        self.layout().addLayout(row)
        self.only_favorites.toggled.connect(self.rebuild)
        self.properties = []
        page = pane._stack.currentWidget()
        for form in page.findChildren(QFormLayout):
            for index in range(form.rowCount()):
                label = form.itemAt(index, QFormLayout.LabelRole)
                field = form.itemAt(index, QFormLayout.FieldRole)
                if not label or not field or not isinstance(label.widget(), QLabel):
                    continue
                widget = field.widget()
                if widget is None and field.layout():
                    widget = next((field.layout().itemAt(i).widget() for i in range(field.layout().count())
                                   if field.layout().itemAt(i).widget()), None)
                if widget is not None and not widget.isHidden():
                    self.properties.append((label.widget().text(), widget))
        self.rebuild()

    def rebuild(self, *_):
        self.entries = []
        for label, widget in sorted(self.properties, key=lambda pair: (pair[0] not in self.favorites, pair[0])):
            if self.only_favorites.isChecked() and label not in self.favorites:
                continue
            self.entries.append((("★ " if label in self.favorites else "") + label,
                                 lambda w=widget: self.jump(w)))
        self.filter()

    def toggle_favorite(self):
        current = self.list.currentItem()
        if current is None:
            return
        label = current.text().removeprefix("★ ")
        self.favorites.symmetric_difference_update((label,))
        self.settings.setValue("favorite_properties", sorted(self.favorites))
        self.rebuild()

    def jump(self, widget):
        child = widget
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QStackedWidget) and isinstance(parent.parentWidget(), QTabWidget):
                parent.parentWidget().setCurrentWidget(child)
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 10, 30)
            child = parent
            parent = parent.parentWidget()
        target = widget if widget.focusPolicy() != Qt.NoFocus else widget.findChild(QWidget)
        if target is not None:
            target.setFocus(Qt.ShortcutFocusReason)
            if isinstance(target, QLineEdit):
                target.selectAll()


def present_search(dialog):
    from azeo_control_trainer.core.presentation.headless import is_headless
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    if not is_headless():
        dialog.show()
        dialog.search.setFocus()
    return dialog
