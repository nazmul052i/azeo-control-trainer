"""Modeless controller discovery and commissioning dialog."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.strategy.engine.controller_discovery import (
    ControllerAdvertisement,
    discover_controllers,
)
from azeo_control_trainer.core.presentation.configuration_chrome import LoadingLine
from azeo_control_trainer.core.presentation.engineering_dialog import (
    EngineeringMenus, button_command, polish_dialog,
)


_ADVERTISEMENT_ROLE = Qt.UserRole + 1


class _DiscoveryWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(discover_controllers())
        except Exception as error:                  # noqa: BLE001
            self.failed.emit(str(error))


class ControllerDiscoveryDialog(QDialog):
    """Find physical controllers, then add or add-and-commission one."""

    def __init__(
            self,
            *,
            add_controller: Callable[[ControllerAdvertisement, bool], bool],
            existing_hardware_ids: set[str] | None = None,
            parent=None,
    ):
        super().__init__(parent)
        self._add_controller = add_controller
        self._existing_hardware_ids = set(existing_hardware_ids or ())
        self._worker: _DiscoveryWorker | None = None
        self.setWindowTitle("Discover Controllers — Control Network")
        self.resize(840, 430)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Discovery identifies controllers on the engineering network. "
            "Add keeps the hardware under Decommissioned Nodes; Add & "
            "Commission associates it with its project node immediately.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Name", "Model", "Hardware ID", "Serial", "Address",
            "OPC UA endpoint", "State",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        self.status = QLabel("Ready to search.")
        layout.addWidget(self.status)
        self.progress = LoadingLine(self)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.discover_button = QPushButton("Discover")
        self.discover_button.clicked.connect(self.discover)
        buttons.addWidget(self.discover_button)
        buttons.addStretch(1)
        self.add_button = QPushButton("Add as Decommissioned")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(lambda: self._add(False))
        buttons.addWidget(self.add_button)
        self.commission_button = QPushButton("Add && Commission")
        self.commission_button.setEnabled(False)
        self.commission_button.clicked.connect(lambda: self._add(True))
        buttons.addWidget(self.commission_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        polish_dialog(self, title="Discover Controllers", mark="search")
        self.menus = EngineeringMenus(self,
            help_text="Discover searches the engineering network. Select a controller to add it as decommissioned, "
            "or add and commission it through the existing project workflow.")
        self.menus.add(self.menus.view, button_command(self.discover_button, mark="search", shortcut="F5"))
        self.menus.menu("&Controller", [button_command(self.add_button, mark="new"),
                                       button_command(self.commission_button, mark="connect")])
        self.menus.table(self.table, lambda row: [button_command(self.add_button, mark="new"),
                                                button_command(self.commission_button, mark="connect")], title="Controller")

    def discover(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.table.setRowCount(0)
        self.discover_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Searching the Control Network…")
        self._worker = _DiscoveryWorker(self)
        self._worker.completed.connect(self._populate)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(
            lambda: self.discover_button.setEnabled(True))
        self._worker.finished.connect(self.progress.hide)
        self._worker.start()

    def _populate(self, advertisements) -> None:
        self.table.setRowCount(0)
        for advertisement in advertisements:
            row = self.table.rowCount()
            self.table.insertRow(row)
            already = advertisement.hardware_id in self._existing_hardware_ids
            state = "Already in project" if already else advertisement.state
            values = (
                advertisement.name,
                advertisement.model,
                advertisement.hardware_id,
                advertisement.serial,
                advertisement.address,
                advertisement.opcua_endpoint,
                state,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(_ADVERTISEMENT_ROLE, advertisement)
                self.table.setItem(row, column, item)
        count = self.table.rowCount()
        self.status.setText(
            f"Found {count} controller{'s' if count != 1 else ''}."
            if count else
            "No controllers replied. Check the engineering network and retry.")
        if count:
            self.table.selectRow(0)

    def _failed(self, message: str) -> None:
        self.status.setText(f"Discovery failed: {message}")

    def _selected(self) -> ControllerAdvertisement | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        value = item.data(_ADVERTISEMENT_ROLE) if item is not None else None
        return value if isinstance(value, ControllerAdvertisement) else None

    def _selection_changed(self) -> None:
        advertisement = self._selected()
        available = advertisement is not None
        already = bool(
            advertisement
            and advertisement.hardware_id in self._existing_hardware_ids)
        self.add_button.setEnabled(available and not already)
        self.commission_button.setEnabled(available)

    def _add(self, commission: bool) -> None:
        advertisement = self._selected()
        if advertisement is None:
            return
        if self._add_controller(advertisement, commission):
            self._existing_hardware_ids.add(advertisement.hardware_id)
            action = "commissioned" if commission else "added as decommissioned"
            self.status.setText(f"{advertisement.name} was {action}.")
            self._selection_changed()
            row = self.table.currentRow()
            if row >= 0:
                self.table.item(row, 6).setText("Already in project")
        else:
            self.status.setText(
                "The controller could not be added. Check for a duplicate "
                "hardware identity or incompatible project node.")

    def closeEvent(self, event) -> None:            # noqa: N802
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(1000)
        super().closeEvent(event)
