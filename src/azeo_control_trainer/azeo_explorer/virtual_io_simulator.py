"""Engineering workbench for project-configured Local Virtual I/O.

The dialog intentionally knows only the driver's optional simulation surface.
It is therefore usable with any provider that implements that contract and
does not import the embedded Azeo plant.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.configuration_chrome import (
    command_bar, icon,
)
from azeo_control_trainer.core.presentation.engineering_dialog import (
    Command, EngineeringMenus, button_command, polish_dialog,
)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, float):
        return f"{value:.6g}"
    return "—" if value is None else str(value)


class _SimulationEditor(QDialog):
    """Small typed editor; outputs never reach this dialog."""

    def __init__(self, row: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Simulate {row['store_tag']}")
        form = QFormLayout(self)
        form.addRow("Signal", QLabel(row["store_tag"]))
        self.mode = AuthoringComboBox()
        self.mode.addItems(["Static", "Sawtooth", "Square", "Sine"])
        self.quality = AuthoringComboBox()
        self.quality.addItems(["Good", "Uncertain", "Bad"])
        lo, hi = row.get("range") or (0.0, 1.0)

        def number(value: float) -> QDoubleSpinBox:
            field = QDoubleSpinBox()
            field.setDecimals(6)
            field.setRange(-1.0e12, 1.0e12)
            field.setValue(float(value))
            return field

        self.value = number(row.get("value") or 0.0)
        self.low = number(lo)
        self.high = number(hi)
        self.period = number(10.0)
        self.period.setMinimum(0.05)
        self.period.setSuffix(" s")
        existing = row.get("simulation") or {}
        if existing:
            self.mode.setCurrentText(str(existing.get("mode", "static")).title())
            self.quality.setCurrentText(
                str(existing.get("quality", "GOOD")).title())
            self.value.setValue(float(existing.get("value", 0.0)))
            self.low.setValue(float(existing.get("low", lo)))
            self.high.setValue(float(existing.get("high", hi)))
            self.period.setValue(float(existing.get("period_s", 10.0)))
        form.addRow("Mode", self.mode)
        form.addRow("Static value", self.value)
        form.addRow("Low", self.low)
        form.addRow("High", self.high)
        form.addRow("Period", self.period)
        form.addRow("Quality", self.quality)
        note = QLabel(
            "AI/DI physics continues underneath this engineering override. "
            "AO/DO channels remain owned by the controller.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        polish_dialog(self, title="Configure input", mark="assign_io")
        self.resize(480, 510)
        self.mode.currentTextChanged.connect(self._sync_fields)
        self._sync_fields()

    def _sync_fields(self):
        static = self.mode.currentText() == "Static"
        self.value.setEnabled(static)
        for field in (self.low, self.high, self.period):
            field.setEnabled(not static)

    def values(self) -> dict[str, Any]:
        return {
            "mode": self.mode.currentText().lower(),
            "value": self.value.value(),
            "low": self.low.value(),
            "high": self.high.value(),
            "period_s": self.period.value(),
            "quality": self.quality.currentText().upper(),
        }


class VirtualIoSimulatorDialog(QDialog):
    """Browse I/O by plant unit and simulate only field-owned inputs."""

    def __init__(self, driver, project_dir: Path, parent=None):
        super().__init__(parent)
        self.driver = driver
        self.project_dir = Path(project_dir)
        self.rows: list[dict[str, Any]] = []
        self.setWindowTitle("Azeo Virtual I/O Simulator")
        self.resize(1120, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(objectName="vioHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 10)
        header_layout.setSpacing(2)
        title = QLabel("Virtual I/O Simulator", objectName="vioTitle")
        header_layout.addWidget(title)
        header_layout.addWidget(QLabel(
            "Commission AI/DI values, quality, and repeatable patterns. "
            "Controller-owned AO/DO channels are monitor-only.",
            objectName="vioSubtitle",
        ))
        root.addWidget(header)

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 8, 12, 8)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter signal, unit, kind, description…")
        self.filter.textChanged.connect(self._apply_filter)
        controls.addWidget(self.filter, 1)
        self.state = QLabel()
        controls.addWidget(self.state)
        root.addLayout(controls)
        self.availability_note = QLabel()
        self.availability_note.setWordWrap(True)
        self.availability_note.setTextFormat(Qt.PlainText)
        self.availability_note.setContentsMargins(12, 0, 12, 6)
        self.availability_note.setAccessibleName("Virtual I/O availability")
        root.addWidget(self.availability_note)
        self.commands = command_bar(self, (
            ("Configure input…", lambda: self.configure_selected(), "assign_io", True),
            ("Release input simulation", lambda: self.release_selected(), "disconnect", False),
            ("Refresh", self.refresh, "restore", False),
        ), more=(
            ("Save scenario…", lambda: self.save_scenario(), "save"),
            ("Load scenario…", lambda: self.load_scenario(), "open"),
        ))
        self.configure_button = self.commands.buttons["Configure input…"]
        self.release_button = self.commands.buttons["Release input simulation"]
        root.addWidget(self.commands)

        split = QSplitter()
        self.units = QTreeWidget()
        self.units.setHeaderLabel("Controller I/O")
        self.units.setMinimumWidth(180)
        self.units.setMaximumWidth(320)
        self.units.itemSelectionChanged.connect(self._apply_filter)
        split.addWidget(self.units)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Signal", "Unit", "I/O", "Value", "Quality", "Simulation",
            "Description",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        # Continuous content sizing turns every live cell/metadata change into
        # another whole-table measurement on a full 558-channel plant.
        self.table.horizontalHeader().setDefaultSectionSize(130)
        self.table.setColumnWidth(0, 180)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemDoubleClicked.connect(
            lambda _item: self.configure_selected())
        split.addWidget(self.table)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 10)
        self.summary = QLabel()
        footer.addWidget(self.summary, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        polish_dialog(self)
        self.menus = EngineeringMenus(self, refresh=self.refresh, search=self.filter,
            help_text="Select a unit or search for a signal. Configure input changes AI/DI value, quality or pattern. "
            "Release input simulation returns that signal to the provider. AO/DO outputs are monitor-only. "
            "Save and load scenarios from File. Start or stop the provider through Explorer > Tools > Virtual I/O.")
        self.menus.add(self.menus.file, Command("Save scenario…", lambda: self.save_scenario(), "save"))
        self.menus.add(self.menus.file, Command("Load scenario…", lambda: self.load_scenario(), "open"))
        self.menus.menu("&Signal", self._signal_commands())
        self.menus.table(self.table, lambda row: self._signal_commands(), title="Virtual I/O")
        self.table.currentCellChanged.connect(
            lambda row, _col, previous, _previous_col: self._sync_buttons() if row != previous else None)

        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def _signal_commands(self):
        return [button_command(self.configure_button), button_command(self.release_button)]

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):  # noqa: N802
        self.timer.stop()
        super().hideEvent(event)

    def _capabilities(self) -> dict[str, Any]:
        get = getattr(self.driver, "simulation_capabilities", None)
        return get() if callable(get) else {
            "available": False,
            "running": bool(getattr(self.driver, "running", False)),
            "reason": "This provider does not expose signal simulation.",
            "active": 0,
        }

    def refresh(self) -> None:
        snapshot = getattr(self.driver, "signal_simulation_snapshot", None)
        self.rows = snapshot() if callable(snapshot) else []
        selected_scope = self._selected_scope()
        topology = tuple(sorted((str(row.get("plant_unit") or "Unassigned"),
                                 str(row.get("kind") or "I/O")) for row in self.rows))
        if topology != getattr(self, "_topology", None):
            self._topology = topology
            self._rebuild_units(selected_scope)
        caps = self._capabilities()
        available = bool(caps.get("available"))
        self.state.setText(("Running" if caps.get("running") else "Ready")
                           if available else "Not available")
        self.state.setObjectName("stateGood" if available else "stateBad")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.state.setToolTip(str(caps.get("reason") or "Provider is ready"))
        reason = str(caps.get("reason") or "Signal simulation is unavailable from this provider.")
        self.availability_note.setText(
            f"{reason} Open Explorer > Tools > Virtual I/O to check the provider, then Refresh."
            if not available else
            "No signals are exposed. Check the project's Virtual I/O mapping, then Refresh.")
        self.availability_note.setVisible(not available or not self.rows)
        self._apply_filter()
        self.summary.setText(
            f"{self.table.rowCount()} of {len(self.rows)} channels · {caps.get('active', 0)} simulated")

    def _rebuild_units(self, selected_scope):
        self.units.blockSignals(True)
        self.units.clear()
        provider_item = QTreeWidgetItem([
            str(getattr(self.driver, "name", "Local Virtual I/O"))])
        provider_item.setData(0, Qt.UserRole, "")
        provider_item.setIcon(0, icon("io_config"))
        self.units.addTopLevelItem(provider_item)
        all_item = QTreeWidgetItem([f"All signals  ({len(self.rows)})"])
        all_item.setData(0, Qt.UserRole, "")
        provider_item.addChild(all_item)
        grouped: dict[str, dict[str, int]] = {}
        for row in self.rows:
            unit = str(row.get("plant_unit") or "Unassigned")
            grouped.setdefault(unit, {})[row.get("kind") or "I/O"] = (
                grouped.setdefault(unit, {}).get(row.get("kind") or "I/O", 0)
                + 1)
        target = all_item
        for unit in sorted(grouped):
            count = sum(grouped[unit].values())
            item = QTreeWidgetItem([f"{unit}  ({count})"])
            item.setData(0, Qt.UserRole, unit)
            item.setIcon(0, icon("open"))
            provider_item.addChild(item)
            for kind, kind_count in sorted(grouped[unit].items()):
                child = QTreeWidgetItem([f"{kind}  ({kind_count})"])
                child.setData(0, Qt.UserRole, f"{unit}\0{kind}")
                child.setIcon(0, icon("assign_io"))
                item.addChild(child)
            if selected_scope == unit:
                target = item
            elif selected_scope.startswith(f"{unit}\0"):
                requested_kind = selected_scope.partition("\0")[2]
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    if str(child.data(0, Qt.UserRole) or "").partition(
                            "\0")[2] == requested_kind:
                        target = child
                        break
        self.units.setCurrentItem(target)
        provider_item.setExpanded(True)
        self.units.blockSignals(False)

    def _selected_scope(self) -> str:
        item = self.units.currentItem()
        value = item.data(0, Qt.UserRole) if item else ""
        return str(value or "")

    def _apply_filter(self) -> None:
        from PySide6.QtCore import QItemSelectionModel
        selected = self._selected_row()
        selected_tag = selected.get("store_tag") if selected else None
        selected_tags = {index.data(Qt.UserRole).get("store_tag")
                         for index in self.table.selectionModel().selectedRows()
                         if isinstance(index.data(Qt.UserRole), dict)}
        scroll = self.table.verticalScrollBar().value()
        query = self.filter.text().strip().lower()
        item = self.units.currentItem()
        scope = str(item.data(0, Qt.UserRole) or "") if item else ""
        unit, _, kind = scope.partition("\0")
        visible = []
        for row in self.rows:
            if unit and str(row.get("plant_unit") or "Unassigned") != unit:
                continue
            if kind and row.get("kind") != kind:
                continue
            haystack = " ".join(str(row.get(key) or "") for key in (
                "store_tag", "signal", "kind", "plant_unit", "description",
            )).lower()
            if query and query not in haystack:
                continue
            visible.append(row)
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(visible))
        selected_index = -1
        provider_available = bool(self._capabilities().get("available"))
        for index, row in enumerate(visible):
            if row["store_tag"] == selected_tag:
                selected_index = index
            profile = row.get("simulation") or {}
            mode = str(profile.get("mode") or (
                "Provider" if provider_available else "Unavailable"))
            if profile:
                mode = f"{mode.title()} · {profile.get('quality', 'GOOD')}"
            values = (
                row["store_tag"], row.get("plant_unit") or "—",
                f"{row.get('kind') or 'I/O'} · "
                f"{'Input' if row.get('simulatable') else 'Output'}",
                _format_value(row.get("value")),
                ("STALE · " if row.get("stale") else "")
                + str(row.get("quality") or "—"),
                mode, row.get("description") or "",
            )
            for column, value in enumerate(values):
                cell = self.table.item(index, column)
                if cell is None:
                    cell = QTableWidgetItem()
                    self.table.setItem(index, column, cell)
                text = str(value)
                if cell.text() != text:
                    cell.setText(text)
                # Selection/commands resolve the row through column zero.
                # Copying live metadata into every cell multiplied each poll.
                if column == 0:
                    cell.setData(Qt.UserRole, row)
        self.table.setCurrentCell(selected_index, 0)
        self.table.clearSelection()
        for index, row in enumerate(visible):
            if row["store_tag"] in selected_tags:
                self.table.selectionModel().select(self.table.model().index(index, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self._sync_buttons()

    def focus_signal(self, tag: str) -> bool:
        """Open a workbench context target without inheriting an old unit filter."""
        self.refresh()
        self.units.setCurrentItem(self.units.topLevelItem(0))
        self.filter.setText(tag)
        self._apply_filter()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.UserRole).get("store_tag") == tag:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return True
        return False

    def _selected_row(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def _sync_buttons(self) -> None:
        row = self._selected_row()
        available = bool(self._capabilities().get("available"))
        simulatable = bool(row and row.get("simulatable"))
        self.configure_button.setEnabled(available and simulatable)
        self.release_button.setEnabled(
            available and simulatable and bool(row.get("simulation")))
        self.summary.setText(f"{self.table.rowCount()} of {len(self.rows)} channels")

    def configure_selected(self, values: dict[str, Any] | None = None) -> bool:
        row = self._selected_row()
        if not self._capabilities().get("available") or not row or not row.get("simulatable"):
            return False
        if values is None:
            if is_headless():
                return False
            editor = _SimulationEditor(row, self)
            if editor.exec() != QDialog.Accepted:
                return False
            values = editor.values()
        try:
            self.driver.configure_signal_simulation(
                row["store_tag"], **values)
        except Exception as error:  # noqa: BLE001
            self._error("Configure Input", error)
            return False
        self._audit("configure", row["store_tag"], values.get("mode", ""))
        self.refresh()
        return True

    def release_selected(self) -> bool:
        row = self._selected_row()
        if not self._capabilities().get("available") or not row or not row.get("simulatable"):
            return False
        try:
            changed = self.driver.clear_signal_simulation(row["store_tag"])
        except Exception as error:  # noqa: BLE001
            self._error("Release Input", error)
            return False
        if changed:
            self._audit("release", row["store_tag"])
        self.refresh()
        return changed

    def _scenario_directory(self) -> Path:
        path = self.project_dir / "virtual_io" / "scenarios"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_scenario(self, path: Path | str | None = None) -> Path | None:
        if path is None:
            if is_headless():
                return None
            selected, _ = QFileDialog.getSaveFileName(
                self, "Save Virtual I/O Scenario",
                str(self._scenario_directory() / "commissioning.json"),
                "Azeo Virtual I/O Scenario (*.json)")
            if not selected:
                return None
            path = selected
        try:
            result = self.driver.save_simulation_profile(path)
        except Exception as error:  # noqa: BLE001
            self._error("Save Scenario", error)
            return None
        self._audit("save_scenario", str(result))
        return result

    def load_scenario(self, path: Path | str | None = None) -> int:
        if path is None:
            if is_headless():
                return 0
            selected, _ = QFileDialog.getOpenFileName(
                self, "Load Virtual I/O Scenario",
                str(self._scenario_directory()),
                "Azeo Virtual I/O Scenario (*.json)")
            if not selected:
                return 0
            path = selected
        try:
            count = self.driver.load_simulation_profile(path)
        except Exception as error:  # noqa: BLE001
            self._error("Load Scenario", error)
            return 0
        self._audit("load_scenario", str(path), str(count))
        self.refresh()
        return count

    def _error(self, title: str, error: Exception) -> None:
        if not is_headless():
            QMessageBox.critical(self, title, str(error))

    @staticmethod
    def _audit(action: str, target: str, detail: str = "") -> None:
        from ..config.logging_config import audit_event
        audit_event(
            "explorer", f"virtual_io_simulator.{action}",
            target=target, detail=detail, outcome="success")


__all__ = ["VirtualIoSimulatorDialog"]
