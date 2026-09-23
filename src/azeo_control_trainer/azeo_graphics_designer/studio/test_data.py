"""Dockable Test Data scenarios for the active Graphics Designer display."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import retain_menu

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF


class TestDataPane(QWidget):
    """Edit render-only overrides; never sends a process write."""

    COL_PATH, COL_VALUE, COL_QUALITY, COL_STATE = range(4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.studio = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        controls = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("MODULE/BLOCK/PARAMETER")
        self.value = QLineEdit("50")
        self.value.setMaximumWidth(100)
        self.quality = AuthoringComboBox()
        self.quality.addItems(("GOOD", "UNCERTAIN", "BAD"))
        self.state = AuthoringComboBox()
        self.state.addItems(("Normal", "Alarm", "Forced"))
        add = QPushButton("Add / Update")
        add.clicked.connect(self.add_override)
        selected = QPushButton("Use Selected")
        selected.clicked.connect(self.use_selected)
        clear = QPushButton("Clear All")
        clear.clicked.connect(self.clear)
        for widget in (self.path, self.value, self.quality, self.state,
                       add, selected, clear):
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("Parameter path", "Preview value", "Quality", "State"))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_PATH, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_VALUE, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_QUALITY, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_STATE, QHeaderView.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.table)
        self.setStyleSheet(
            f"background: {WF['pane']}; color: {WF['tx']};")

    def set_studio(self, studio) -> None:
        self.studio = studio
        self.reload()

    @staticmethod
    def _scalar(text: str):
        value = str(text).strip()
        if value.casefold() in ("true", "on"):
            return True
        if value.casefold() in ("false", "off"):
            return False
        try:
            return float(value)
        except ValueError:
            return value

    def add_override(self) -> bool:
        if self.studio is None:
            return False
        path = self.path.text().strip()
        if not path:
            return False
        state = self.state.currentText()
        self.studio.preview_source.set_override(
            path,
            value=self._scalar(self.value.text()),
            quality=self.quality.currentText(),
            alarm_active=state == "Alarm",
            alarm_acked=False,
            alarm_priority=15 if state == "Alarm" else 0,
            alarm_condition="HI_HI" if state == "Alarm" else "",
            forced=state == "Forced",
        )
        self.studio.preview_source.enabled = self.studio.test_mode
        self.studio.engine.poll()
        self.reload()
        return True

    def use_selected(self) -> str:
        if self.studio is None:
            return ""
        selected = self.studio.canvas.scene().selectedItems()
        if not selected:
            return ""
        item = selected[0]
        binding = getattr(item, "binding", None)
        path = str(getattr(binding, "path", "") or "")
        if not path:
            params = getattr(getattr(item, "pvm", None), "params", {})
            path = str(next(iter(params.values()), ""))
        self.path.setText(path)
        return path

    def clear(self) -> None:
        if self.studio is not None:
            self.studio.preview_source.clear()
            self.studio.engine.poll()
        self.reload()

    def reload(self) -> None:
        rows = {} if self.studio is None \
            else self.studio.preview_source.overrides
        self.table.setRowCount(0)
        for path, data in sorted(rows.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            quality = data.get("quality", "GOOD")
            quality = getattr(quality, "name", quality)
            state = "Alarm" if data.get("alarm_active") \
                else "Forced" if data.get("forced") else "Normal"
            for column, value in enumerate((
                    path, data.get("value", ""), quality, state)):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole, path)
                self.table.setItem(row, column, cell)

    def _menu(self, pos) -> None:
        row = self.table.itemAt(pos)
        if row is None or self.studio is None:
            return
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu
        menu = studio_menu("TEST DATA", row.data(Qt.UserRole))
        remove = menu.addAction("Remove preview override")
        remove.triggered.connect(
            lambda: (self.studio.preview_source.clear_override(
                row.data(Qt.UserRole)), self.studio.engine.poll(),
                self.reload()))
        retain_menu(self, menu, "_context_menu")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.table.viewport().mapToGlobal(pos))
