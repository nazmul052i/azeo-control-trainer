"""Plant diagnostics and instructor disturbances over the provider protocol."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)


class PlantTools:
    def __init__(self, window):
        self.window = window
        self.service = window.service
        self.loaded = False
        self.available = False
        self.catalog = {}
        self.state = {}
        self.unit_page, layout = window._tab()
        self.note = QLabel("Starting the configured plant provider…")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.units = window._table(["Unit", "Process", "Signals", "Active disturbances", "Bad signals"], 1)
        layout.addWidget(self.units)
        window.tabs.addTab(self.unit_page, "Plant units")

        self.fault_page, layout = window._tab()
        layout.addWidget(QLabel("Inject equipment and process faults for a training exercise. Changes take effect in the running plant."))
        self.fault_unit = QComboBox()
        self.fault_unit.addItem("All plant units", "")
        self.fault_unit.currentIndexChanged.connect(self._filter_faults)
        layout.addWidget(self.fault_unit)
        self.faults = window._table(["Unit", "Equipment", "Disturbance", "State", "Value"], 2)
        self.faults.itemSelectionChanged.connect(self._select_fault)
        layout.addWidget(self.faults)
        self.selection_note = QLabel("Select a disturbance to configure its setting.")
        self.selection_note.setWordWrap(True)
        layout.addWidget(self.selection_note)
        editor = QHBoxLayout()
        self.active = QCheckBox("Active")
        editor.addWidget(self.active)
        self.value_label = QLabel("Value")
        self.value_label.setWordWrap(True)
        editor.addWidget(self.value_label)
        self.value = QLineEdit()
        self.value.setMaximumWidth(140)
        self.value.setAccessibleName("Disturbance value")
        editor.addWidget(self.value)
        editor.addStretch(1)
        self.apply = QPushButton("Apply disturbance")
        self.apply.setObjectName("primary")
        self.apply.setProperty("configurationIconName", "params")
        self.apply.clicked.connect(self.apply_selected)
        editor.addWidget(self.apply)
        self.clear = QPushButton("Clear selected")
        self.clear.setProperty("configurationIconName", "restore")
        self.clear.clicked.connect(self.clear_selected)
        editor.addWidget(self.clear)
        layout.addLayout(editor)
        window.tabs.addTab(self.fault_page, "Disturbances")

        self.parameter_page, layout = window._tab()
        label = QLabel("Model constants and their engineering basis. These values describe the compiled process model and are read-only.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a unit, parameter or engineering basis…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_parameters)
        layout.addWidget(self.search)
        self.parameters = window._table(["Unit", "Parameter", "Value", "Units", "Engineering basis"], 4)
        layout.addWidget(self.parameters)
        window.tabs.addTab(self.parameter_page, "Model parameters")
        for table in (self.units, self.faults, self.parameters):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._select_fault()

    @staticmethod
    def _set(table, row, column, text):
        from PySide6.QtWidgets import QTableWidgetItem
        text = str(text)
        item = table.item(row, column)
        if item is None:
            table.setItem(row, column, QTableWidgetItem(text))
        elif item.text() != text:
            item.setText(text)

    def refresh(self, capabilities):
        available = bool(capabilities.get("available") and capabilities.get("model_diagnostics_supported"))
        self.available = available and bool(capabilities.get("disturbances_supported"))
        self.apply.setEnabled(self.available and self._selected() is not None)
        self.clear.setEnabled(self.available and self._selected() is not None)
        for table in (self.units, self.faults, self.parameters):
            table.setEnabled(available)
        if not available:
            self.loaded = False
            self.note.setText(capabilities.get("reason") or "This provider does not expose model diagnostics.")
            return
        try:
            if not self.loaded:
                self.catalog = self.service.driver.process_model_catalog()
                self._populate()
                self.loaded = True
            self.state = self.service.driver.process_model_state()
        except Exception as error:  # noqa: BLE001
            self.available = False
            self.note.setText(f"Plant diagnostics unavailable: {error}")
            self.apply.setEnabled(False)
            self.clear.setEnabled(False)
            return
        states = {row["unit"]: row for row in self.state.get("units", ())}
        for index, row in enumerate(self.catalog.get("units", ())):
            live = states.get(row["unit"], {})
            self._set(self.units, index, 3, live.get("active_disturbances", "—"))
            self._set(self.units, index, 4, live.get("bad_signals", "—"))
        faults = {row["id"]: row for row in self.state.get("disturbances", ())}
        for index, row in enumerate(self.catalog.get("disturbances", ())):
            live = faults.get(row["id"], {})
            self._set(self.faults, index, 3, "Active" if live.get("active") else "Clear")
            self._set(self.faults, index, 4, f"{live.get('value', 0):g}" if row["parameter"] else "—")
        core = "C++" if capabilities.get("core") == "cpp" else str(capabilities.get("core", "Provider"))
        self.note.setText(f"{core} plant · {len(states)} units · "
                          f"{sum(row['signals'] for row in self.catalog.get('units', ()))} signals · "
                          f"{sum(bool(row.get('active')) for row in faults.values())} active disturbances")
        sampled_at = self.state.get("sampled_at")
        if capabilities.get("running") and sampled_at and time.monotonic() - sampled_at > 3.0:
            self.note.setText("Waiting for the plant to update — showing the last received status.")

    def _populate(self):
        rows = self.catalog.get("units", ())
        self.units.setRowCount(len(rows))
        self.fault_unit.blockSignals(True)
        self.fault_unit.clear()
        self.fault_unit.addItem("All plant units", "")
        for index, row in enumerate(rows):
            for column, field in enumerate(("unit", "name", "signals")):
                self._set(self.units, index, column, row[field])
            self.fault_unit.addItem(f"{row['unit']} · {row['name']}", row["unit"])
        self.fault_unit.blockSignals(False)
        rows = self.catalog.get("disturbances", ())
        self.faults.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, field in enumerate(("unit", "target", "description")):
                self._set(self.faults, index, column, row[field])
            self.faults.item(index, 0).setData(Qt.UserRole, row)
        rows = self.catalog.get("parameters", ())
        self.parameters.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (row["unit"], row["name"], f"{row['value']:.6g}", row["eu"], row["description"])
            for column, value in enumerate(values):
                self._set(self.parameters, index, column, value)
        self._filter_faults()
        self._filter_parameters()

    def _filter_faults(self):
        unit = self.fault_unit.currentData()
        self.faults.clearSelection()
        self.faults.setCurrentCell(-1, -1)
        for index, row in enumerate(self.catalog.get("disturbances", ())):
            self.faults.setRowHidden(index, bool(unit and row["unit"] != unit))
        self._select_fault()

    def _filter_parameters(self):
        needle = self.search.text().strip().casefold()
        for index in range(self.parameters.rowCount()):
            haystack = " ".join(self.parameters.item(index, column).text()
                                for column in range(self.parameters.columnCount())).casefold()
            self.parameters.setRowHidden(index, needle not in haystack)

    def _selected(self):
        index = self.faults.currentRow()
        item = self.faults.item(index, 0) if index >= 0 and not self.faults.isRowHidden(index) else None
        return item.data(Qt.UserRole) if item is not None else None

    def _select_fault(self):
        row = self._selected()
        self.apply.setEnabled(self.available and row is not None)
        self.clear.setEnabled(self.available and row is not None)
        self.active.setEnabled(self.available and row is not None)
        self.value.setEnabled(bool(self.available and row and row["parameter"]))
        self.value_label.setText("Value")
        self.selection_note.setText(
            f"{row['description']} · {row['parameter']} ({row['minimum']:g} to {row['maximum']:g})"
            if row and row["parameter"] else row["description"] if row else
            "Select a disturbance to configure its setting.")
        if row:
            live = next((item for item in self.state.get("disturbances", ()) if item["id"] == row["id"]), {})
            self.active.setChecked(bool(live.get("active")))
            setting = live.get("value", row["minimum"])
            if row["parameter"] and not live.get("active"):
                setting = max(row["minimum"], min(row["maximum"], setting))
            self.value.setText(f"{setting:g}")
            self.value.setPlaceholderText(f"{row['minimum']:g} to {row['maximum']:g}")
            self.value.setToolTip(f"Allowed range: {row['minimum']:g} to {row['maximum']:g}")

    def apply_selected(self):
        row = self._selected()
        if row is None:
            return
        def apply():
            value = float(self.value.text()) if row["parameter"] else None
            self.service.set_plant_disturbance(row["id"], self.active.isChecked(), value)
        self.window._run("Apply disturbance", apply)

    def clear_selected(self):
        row = self._selected()
        if row is not None:
            self.window._run("Clear disturbance", lambda:
                             self.service.set_plant_disturbance(row["id"], False))
            self._select_fault()
