"""Explorer's system-level Simulation Workbench.

This dialog exposes the Qt-free orchestration service.  It complements the
single-signal Virtual I/O editor: the workbench is where an engineer manages a
whole simulated control system, its process clock, snapshots and playback.
"""
from __future__ import annotations

import getpass
import time
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.simulation.workbench import SimulationScope, SimulationWorkbenchService
from azeo_control_trainer.config.paths import data_dir
from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.configuration_chrome import command_bar, icon
from azeo_control_trainer.core.presentation.engineering_dialog import (
    Command, ENGINEERING_QSS, EngineeringMenus, button_command, polish_dialog,
)


_QSS = ENGINEERING_QSS


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, float):
        return f"{value:.6g}"
    return "—" if value is None or value == "" else str(value)


@contextmanager
def _table_update(table):
    updates = table.updatesEnabled()
    blocked = table.blockSignals(True)
    table.setUpdatesEnabled(False)
    try:
        yield
    finally:
        table.setUpdatesEnabled(updates)
        table.blockSignals(blocked)


class SimulationWorkbenchDialog(QDialog):
    """Commission and reproduce one controller/process simulation session."""

    def __init__(self, store, driver, project_dir: Path, parent=None,
                 executive=None, plant_only=False):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(
            "simulator" if plant_only else "simulation_workbench"))
        self.plant_only = plant_only
        self.project_dir = Path(project_dir).resolve()
        journal_path = (data_dir() / "simulation" / self.project_dir.name /
                        "operator_changes.sqlite")
        self.service = SimulationWorkbenchService(
            store, driver, self.project_dir, actor=getpass.getuser(),
            executive=executive, journal_path=journal_path)
        self._playback_position = 0
        self.setWindowTitle("Plant Simulator — Azeo Simulation Workbench" if plant_only else "Azeo Simulation Workbench")
        self.resize(1280, 760)
        self.setStyleSheet(_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget(objectName="vioHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 11, 16, 9)
        header_layout.setSpacing(2)
        header_layout.addWidget(QLabel("Plant Simulator" if plant_only else "Simulation Workbench", objectName="vioTitle"))
        header_layout.addWidget(QLabel(
            "Operate the training plant, inject disturbances, inspect model parameters and manage snapshots."
            if plant_only else "Scope controller simulation, exercise Virtual I/O, manage the "
            "process clock, and reproduce changes from one workspace.",
            objectName="vioSubtitle"))
        root.addWidget(header)

        clock = QHBoxLayout()
        clock.setContentsMargins(12, 8, 12, 8)
        self.process_state = QLabel()
        clock.addWidget(self.process_state)
        clock.addStretch(1)
        self.clock_buttons: dict[str, QPushButton] = {}
        for label, handler in (("Pause", self.pause), ("Run", self.resume),
                               ("Step", self.step)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, callback=handler: callback())
            self.clock_buttons[label] = button
            clock.addWidget(button)
        clock.addWidget(QLabel("Speed"))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 20.0)
        self.speed.setSingleStep(0.1)
        self.speed.setSuffix(" ×")
        self.speed.setValue(1.0)
        self.speed.valueChanged.connect(self._speed_changed)
        clock.addWidget(self.speed)
        root.addLayout(clock)
        self.availability_note = QLabel()
        self.availability_note.setTextFormat(Qt.PlainText)
        self.availability_note.setWordWrap(True)
        self.availability_note.setContentsMargins(12, 0, 12, 6)
        self.availability_note.setAccessibleName("Simulation availability")
        root.addWidget(self.availability_note)

        split = QSplitter()
        self.scope_tree = QTreeWidget()
        self.scope_tree.setHeaderLabel("Simulation scope")
        self.scope_tree.setMinimumWidth(180)
        self.scope_tree.setMaximumWidth(320)
        self.scope_tree.itemSelectionChanged.connect(self.refresh_tables)
        split.addWidget(self.scope_tree)
        self.scope_tree.setVisible(not plant_only)
        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        if plant_only:
            from .plant_tools import PlantTools
            self.plant_tools = PlantTools(self)
        else:
            self._build_setup_tab()
            self._build_io_tab()
        self._build_virtual_io_tab()
        if not plant_only:
            self._build_other_tab()
        self._build_snapshot_tab()
        if not plant_only:
            self._build_playback_tab()
        self._build_diagnostics_tab()
        if not plant_only:
            self.record_button.blockSignals(True)
            self.record_button.setChecked(self.service.recording)
            self.record_button.setText(
                "Stop Recording" if self.service.recording else "Start Recording")
            self.record_button.blockSignals(False)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 7, 12, 9)
        self.summary = QLabel()
        footer.addWidget(self.summary, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        footer.addWidget(refresh)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        polish_dialog(self)
        for label in self.findChildren(QLabel):
            if len(label.text()) > 80:
                label.setWordWrap(True)
        self.menus = EngineeringMenus(self, refresh=self.refresh,
            help_text="Use Run, Pause, Step and Speed for the shared process clock. Disturbances affect the actual plant. "
            "Model parameters are read-only. Virtual I/O shows live signal values and quality. "
            "Snapshots restore the process condition. Control engineering remains in Control Designer."
            if plant_only else "Select the entire system or a module in Simulation Scope. Setup commands apply to that scope. "
            "Process controls manage the training clock. Snapshots can restore operating, tuning and process state. "
            "Operator Playback applies recorded events through the existing simulation service.")
        from azeo_control_trainer.core.presentation.product_help import add_help_action
        add_help_action(self.menus.help_menu, self)
        self.menus.menu("&Process", [button_command(button, mark=mark) for button, mark in (
            (self.clock_buttons["Pause"], "pause"), (self.clock_buttons["Run"], "connect"),
            (self.clock_buttons["Step"], "step_block"))])
        self.menus.add(self.menus.file, button_command(self.save_snapshot_button))
        self.menus.add(self.menus.view, button_command(self.open_signal_button))
        for table in self.findChildren(QTableWidget):
            self.menus.table(table, title="Simulation Workbench")
        if not plant_only:
            self.menus.table(self.io_table, lambda row: [button_command(self.apply_io_button, mark="params")],
                             title="Controller I/O", identity_columns=(0, 1))
        else:
            self.menus.table(self.plant_tools.units, self._unit_commands, title="Plant unit")
            self.menus.table(self.plant_tools.faults, lambda row: [
                button_command(self.plant_tools.apply, mark="params"),
                button_command(self.plant_tools.clear, mark="restore")],
                title="Plant disturbance", identity_columns=(0, 1, 2))
        self.menus.table(self.virtual_table, self._signal_commands, title="Plant signal")
        self.menus.table(self.snapshot_table, lambda row: [button_command(self.restore_button, mark="restore")],
                         title="Simulation snapshot", identity_columns=(0, 1))
        self.open_signal_button.setEnabled(callable(getattr(driver, "simulation_capabilities", None)))

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_live)
        self.refresh()

    def _unit_commands(self, row):
        unit = self.plant_tools.units.item(row, 0).text()
        return [Command(title, lambda page=page: self._show_unit(unit, page), mark,
                        command_id="plant." + page)
                for page, title, mark in (
                    ("signals", "View unit signals", "io_config"),
                    ("disturbances", "Configure unit disturbances", "params"),
                    ("parameters", "Inspect unit model parameters", "properties"))]

    def _show_unit(self, unit, page):
        if page == "signals":
            self.virtual_filter.setText(unit)
            self.tabs.setCurrentWidget(self.virtual_page)
        elif page == "disturbances":
            self.plant_tools.fault_unit.setCurrentIndex(self.plant_tools.fault_unit.findData(unit))
            self.tabs.setCurrentWidget(self.plant_tools.fault_page)
        else:
            self.plant_tools.search.setText(unit)
            self.tabs.setCurrentWidget(self.plant_tools.parameter_page)

    def _signal_commands(self, row):
        tag = self.virtual_table.item(row, 0).text()
        return [Command("Open in Signal Simulator…", lambda: self._open_signal_simulator(tag=tag),
                        "io_config", self.open_signal_button.isEnabled, command_id="virtual_io.inspect")]

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.refresh_live()
        self.timer.start()

    def hideEvent(self, event):  # noqa: N802
        self.timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _table(headers: list[str], stretch: int = -1) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().hide()
        # Live value changes must not remeasure hundreds of rows for every
        # cell. That kept the event loop busy for seconds on the Virtual I/O tab.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setDefaultSectionSize(130)
        for column, title in enumerate(headers):
            table.setColumnWidth(column, max(100, table.fontMetrics().horizontalAdvance(title) + 36))
        if headers[0] != "Unit":
            table.setColumnWidth(0, max(180, table.columnWidth(0)))
        table.setWordWrap(False)
        if stretch >= 0:
            table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
        return table

    def _tab(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        return page, layout

    def _build_setup_tab(self) -> None:
        page, layout = self._tab()
        layout.addWidget(QLabel("SCOPED CONTROLLER CHECKOUT", objectName="section"))
        layout.addWidget(QLabel(
            "Enable simulation only for the selected module scope. Setup mode "
            "moves blocks with modes to their configured setup mode (Manual by "
            "default); Normal mode returns each to its configured normal mode."))
        self.setup_commands = command_bar(self, (
            ("Enable Simulate", lambda: self._bulk_simulate(True), "connect", False),
            ("Disable Simulate", lambda: self._bulk_simulate(False), "disconnect", False),
        ), more=(
            ("Setup Mode", self._setup_mode, "params"),
            ("Normal Mode", self._normal_mode, "params"),
            ("Initialize Dynamic Blocks", self._initialize, "restore"),
        ))
        layout.addWidget(self.setup_commands)
        self.module_table = self._table(
            ["Module", "State", "Execution", "Blocks"], stretch=0)
        self.module_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.module_table, 1)
        self.tabs.addTab(page, "Setup")

    def _build_io_tab(self) -> None:
        page, layout = self._tab()
        top = QHBoxLayout()
        top.addWidget(QLabel(
            "Edit Simulate, value, quality, or mode, then apply the selected row."), 1)
        apply_button = QPushButton("Apply Selected Row")
        self.apply_io_button = apply_button
        apply_button.setEnabled(False)
        apply_button.setObjectName("primary")
        apply_button.clicked.connect(self.apply_io_row)
        top.addWidget(apply_button)
        layout.addLayout(top)
        self.io_table = self._table([
            "Module", "Block", "Type", "Tag", "Sim", "Sim value",
            "Sim quality", "Mode", "OUT", "Status"], stretch=3)
        self.io_table.itemSelectionChanged.connect(lambda: apply_button.setEnabled(bool(self.io_table.selectedItems())))
        layout.addWidget(self.io_table)
        self.tabs.addTab(page, "I/O Blocks")

    def _build_virtual_io_tab(self) -> None:
        page, layout = self._tab()
        self.virtual_page = page
        top = QHBoxLayout()
        top.addWidget(QLabel(
            "Configured Local Virtual I/O references. Inputs may be overridden; "
            "outputs remain controller-owned."), 1)
        self.open_signal_button = QPushButton("Open Signal Simulator…")
        self.open_signal_button.setProperty("configurationIconName", "io_config")
        self.open_signal_button.setProperty("commandId", "virtual_io.open")
        self.open_signal_button.clicked.connect(self._open_signal_simulator)
        top.addWidget(self.open_signal_button)
        layout.addLayout(top)
        self.virtual_filter = QLineEdit()
        self.virtual_filter.setPlaceholderText("Filter signals by tag, unit or description…")
        self.virtual_filter.setClearButtonEnabled(True)
        self.virtual_filter.textChanged.connect(self._filter_virtual_rows)
        layout.addWidget(self.virtual_filter)
        self.virtual_table = self._table([
            "Store tag", "Provider signal", "Unit", "I/O", "Value", "Quality",
            "Override", "Description"], stretch=7)
        self.virtual_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.virtual_table)
        self.tabs.addTab(page, "Virtual I/O")

    def _build_other_tab(self) -> None:
        page, layout = self._tab()
        layout.addWidget(QLabel(
            "Non-I/O function blocks in the selected scope. Outputs are live "
            "diagnostic values, not writable fields."))
        self.other_table = self._table(
            ["Module", "Block", "Type", "Status", "Outputs"], stretch=4)
        self.other_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.other_table)
        self.tabs.addTab(page, "Other Modules")

    def _build_snapshot_tab(self) -> None:
        page, layout = self._tab()
        options = QHBoxLayout()
        self.restore_operating = QCheckBox("Operating")
        self.restore_operating.setChecked(True)
        self.restore_tuning = QCheckBox("Tuning")
        self.restore_tuning.setChecked(True)
        self.restore_process = QCheckBox("Process")
        self.restore_process.setChecked(True)
        if self.plant_only:
            self.restore_operating.setChecked(False)
            self.restore_tuning.setChecked(False)
        for check in (self.restore_operating, self.restore_tuning,
                      self.restore_process):
            options.addWidget(check)
        if self.plant_only:
            self.restore_operating.hide()
            self.restore_tuning.hide()
        options.addStretch(1)
        save = QPushButton("Save Snapshot…")
        self.save_snapshot_button = save
        save.setProperty("configurationIconName", "save")
        save.setProperty("commandId", "snapshot.save")
        save.setObjectName("primary")
        save.clicked.connect(lambda: self.save_snapshot())
        options.addWidget(save)
        restore = QPushButton("Restore Selected")
        self.restore_button = restore
        restore.setEnabled(False)
        restore.clicked.connect(lambda: self.restore_snapshot())
        options.addWidget(restore)
        layout.addLayout(options)
        self.snapshot_table = self._table(
            ["Name", "Saved", "Modules", "Process", "Note"], stretch=4)
        self.snapshot_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.snapshot_table.itemSelectionChanged.connect(self._sync_restore)
        for checkbox in (self.restore_operating, self.restore_tuning, self.restore_process):
            checkbox.toggled.connect(self._sync_restore)
        layout.addWidget(self.snapshot_table)
        self.tabs.addTab(page, "Snapshots")

    def _sync_restore(self):
        self.restore_button.setEnabled(bool(self.snapshot_table.selectedItems()) and any(
            checkbox.isChecked() for checkbox in (self.restore_operating, self.restore_tuning, self.restore_process)))

    def _build_playback_tab(self) -> None:
        page, layout = self._tab()
        controls = QHBoxLayout()
        self.record_button = QPushButton("Start Recording")
        self.record_button.setCheckable(True)
        self.record_button.toggled.connect(self._toggle_recording)
        controls.addWidget(self.record_button)
        marker = QPushButton("Add Marker…")
        marker.clicked.connect(lambda: self.add_marker())
        controls.addWidget(marker)
        controls.addStretch(1)
        restart = QPushButton("Restart Playback")
        restart.clicked.connect(self.restart_playback)
        controls.addWidget(restart)
        next_button = QPushButton("Apply Next Event")
        next_button.setObjectName("primary")
        next_button.clicked.connect(self.play_next)
        controls.addWidget(next_button)
        layout.addLayout(controls)
        self.playback_table = self._table([
            "#", "Simulation time", "Actor", "Action", "Target", "Value",
            "Marker / note"], stretch=4)
        self.playback_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.playback_table)
        self.tabs.addTab(page, "Operator Playback")

    def _build_diagnostics_tab(self) -> None:
        page, layout = self._tab()
        self.diagnostics_table = self._table(["Item", "Value"], stretch=1)
        self.diagnostics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.diagnostics_table)
        self.tabs.addTab(page, "Diagnostics")

    # ---------------------------------------------------------------- refresh
    def _scope(self) -> SimulationScope:
        item = self.scope_tree.currentItem()
        module = str(item.data(0, Qt.UserRole) or "") if item else ""
        return SimulationScope((module,)) if module else SimulationScope()

    def refresh(self) -> None:
        if self.plant_only:
            self.plant_tools.loaded = False
            self._refresh_virtual_table()
            self._refresh_snapshots()
            self.refresh_live()
            return
        selected = self._scope().modules
        modules = self.service.modules()
        self.scope_tree.blockSignals(True)
        self.scope_tree.clear()
        root = QTreeWidgetItem([f"Entire control system  ({len(modules)})"])
        root.setData(0, Qt.UserRole, "")
        root.setIcon(0, icon("io_config"))
        self.scope_tree.addTopLevelItem(root)
        target = root
        for row in modules:
            item = QTreeWidgetItem([row["name"]])
            item.setData(0, Qt.UserRole, row["name"])
            item.setIcon(0, icon("module_props"))
            root.addChild(item)
            if selected and row["name"] == selected[0]:
                target = item
        root.setExpanded(True)
        self.scope_tree.setCurrentItem(target)
        self.scope_tree.blockSignals(False)
        self.refresh_tables()
        self._refresh_snapshots()
        self._refresh_playback()
        self.refresh_live()

    def refresh_tables(self) -> None:
        self._refresh_virtual_table()
        if self.plant_only:
            return
        scope = self._scope()
        modules = [row for row in self.service.modules()
                   if scope.all_modules or row["name"] in scope.modules]
        self.module_table.setRowCount(len(modules))
        for row_index, row in enumerate(modules):
            values = (row["name"], "ONLINE" if row["online"] else "OFFLINE",
                      "PAUSED" if row["paused"] else "RUNNING", row["blocks"])
            for column, value in enumerate(values):
                self.module_table.setItem(row_index, column,
                                          QTableWidgetItem(str(value)))

        rows = self.service.io_rows(scope)
        self.io_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (row["module"], row["block"], row["type"], row["tag"],
                      "", _text(row["sim_value"]), row["sim_quality"],
                      row["mode"], _text(row["out"]), row["status"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                if column == 4:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if row["simulate"] else Qt.Unchecked)
                elif column not in {5, 6, 7}:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.io_table.setItem(row_index, column, item)

        other = self.service.other_rows(scope)
        self.other_table.setRowCount(len(other))
        for row_index, row in enumerate(other):
            outputs = ", ".join(f"{key}={_text(value)}"
                                for key, value in row["outputs"].items())
            values = (row["module"], row["block"], row["type"],
                      row["status"], outputs)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.other_table.setItem(row_index, column, item)

    def _refresh_virtual_table(self):
        snapshot = getattr(self.service.driver, "signal_simulation_snapshot", None)
        rows = snapshot() if callable(snapshot) else []
        with _table_update(self.virtual_table):
            self.virtual_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                profile = row.get("simulation") or {}
                values = (row.get("store_tag"), row.get("signal"),
                          row.get("plant_unit"), row.get("kind"),
                          _text(row.get("value")), row.get("quality"),
                          profile.get("mode", "Live physics"), row.get("description"))
                for column, value in enumerate(values):
                    item = self.virtual_table.item(row_index, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.virtual_table.setItem(row_index, column, item)
                    text = _text(value)
                    if item.text() != text:
                        item.setText(text)
                    if column == 0:
                        item.setData(Qt.UserRole, row)
        self._filter_virtual_rows()

    def _filter_virtual_rows(self):
        needle = self.virtual_filter.text().strip().casefold()
        with _table_update(self.virtual_table):
            for row in range(self.virtual_table.rowCount()):
                haystack = " ".join(self.virtual_table.item(row, c).text() for c in (0, 1, 2, 3, 7))
                self.virtual_table.setRowHidden(row, needle not in haystack.casefold())

    def _refresh_visible_live_values(self) -> None:
        """Refresh indications without overwriting an engineer's table edits."""
        current = self.tabs.currentWidget()
        if self.plant_only:
            self.plant_tools.refresh(self.service.process_capabilities())
        if not self.plant_only and current is self.tabs.widget(0):
            modules = {row["name"]: row for row in self.service.modules()}
            for row_index in range(self.module_table.rowCount()):
                name = self.module_table.item(row_index, 0).text()
                row = modules.get(name)
                if row:
                    self.module_table.item(row_index, 1).setText(
                        "ONLINE" if row["online"] else "OFFLINE")
                    self.module_table.item(row_index, 2).setText(
                        "PAUSED" if row["paused"] else "RUNNING")
        elif not self.plant_only and current is self.tabs.widget(1):
            rows = {(row["module"], row["block_id"]): row
                    for row in self.service.io_rows(self._scope())}
            for row_index in range(self.io_table.rowCount()):
                source = self.io_table.item(row_index, 0)
                record = source.data(Qt.UserRole) if source else None
                if not record:
                    continue
                live = rows.get((record["module"], record["block_id"]))
                if live:
                    self.io_table.item(row_index, 8).setText(_text(live["out"]))
                    self.io_table.item(row_index, 9).setText(str(live["status"]))
        elif current is self.virtual_page:
            with _table_update(self.virtual_table):
                self._refresh_virtual_values()
        elif not self.plant_only and current is self.tabs.widget(3):
            rows = {(row["module"], row["block_id"]): row
                    for row in self.service.other_rows(self._scope())}
            for row_index in range(self.other_table.rowCount()):
                source = self.other_table.item(row_index, 0)
                record = source.data(Qt.UserRole) if source else None
                if not record:
                    continue
                live = rows.get((record["module"], record["block_id"]))
                if live:
                    outputs = ", ".join(
                        f"{key}={_text(value)}"
                        for key, value in live["outputs"].items())
                    self.other_table.item(row_index, 3).setText(
                        str(live["status"]))
                    self.other_table.item(row_index, 4).setText(outputs)

    def _refresh_virtual_values(self):
        snapshot = getattr(
            self.service.driver, "signal_simulation_snapshot", None)
        rows = {row["store_tag"]: row for row in snapshot()} \
            if callable(snapshot) else {}
        for row_index in range(self.virtual_table.rowCount()):
            source = self.virtual_table.item(row_index, 0)
            record = source.data(Qt.UserRole) if source else None
            live = rows.get(record.get("store_tag")) if record else None
            if live:
                profile = live.get("simulation") or {}
                for column, text in (
                        (4, _text(live.get("value"))),
                        (5, _text(live.get("quality"))),
                        (6, _text(profile.get("mode", "Live physics")))):
                    item = self.virtual_table.item(row_index, column)
                    if item.text() != text:
                        item.setText(text)

    def refresh_live(self) -> None:
        caps = self.service.process_capabilities()
        available = bool(caps.get("available"))
        process_running = bool(caps.get("running"))
        controller_running = bool(getattr(
            self.service.executive, "is_running", False))
        state = "UNAVAILABLE" if not available else (
            "RUNNING" if process_running else "PAUSED")
        self.process_state.setText(
            f"● PROCESS {state}" + (
                f"  ·  t={float(caps['sim_time']):.1f} s"
                if available and caps.get("sim_time") is not None else ""))
        style_name = "stateGood" if available else "stateBad"
        if self.process_state.objectName() != style_name:
            self.process_state.setObjectName(style_name)
            self.process_state.style().unpolish(self.process_state)
            self.process_state.style().polish(self.process_state)
        self.process_state.setToolTip(str(caps.get("reason") or "Provider ready"))
        self.speed.blockSignals(True)
        if not self.speed.hasFocus() and not self.speed.lineEdit().hasFocus():
            self.speed.setValue(float(caps.get("speed_factor") or 1.0))
        self.speed.blockSignals(False)
        self.speed.setEnabled(available)
        self.clock_buttons["Pause"].setEnabled(
            available and (process_running or controller_running))
        self.clock_buttons["Run"].setEnabled(
            available and not process_running)
        self.clock_buttons["Step"].setEnabled(
            available and bool(caps.get("step_supported"))
            and not process_running and not controller_running)
        self._refresh_visible_live_values()
        # This surface has no playback journal; SQLite can wait seconds behind
        # another writer and must not be polled from its one-second UI timer.
        diagnostics = self.service.diagnostics(include_journal=not self.plant_only)
        self._refresh_diagnostics(diagnostics)
        reason = str(caps.get("reason") or "The process provider is unavailable.")
        self.availability_note.setText(
            f"{reason} Open Explorer > Tools > Virtual I/O to check the provider, then Refresh."
            if not available else
            "No control modules are online. Open the intended module in Control Designer and go Online, then Refresh.")
        self.availability_note.setVisible(not available or not diagnostics["online_modules"])
        self.summary.setText(
            f"{diagnostics['online_modules']}/{diagnostics['modules']} modules online"
            f"  ·  {diagnostics['controller_io']} controller I/O"
            f"  ·  {diagnostics['simulated_controller_io']} simulated"
            f"  ·  {diagnostics['virtual_io_channels']} Virtual I/O channels")

    # -------------------------------------------------------------- operations
    def _run(self, title: str, callback) -> Any:
        try:
            result = callback()
        except Exception as error:  # noqa: BLE001
            if not is_headless():
                QMessageBox.critical(self, title, str(error))
            return None
        self.refresh_tables()
        self.refresh_live()
        return result

    def pause(self) -> None:
        self._run("Pause Process", self.service.pause)

    def resume(self) -> None:
        self._run("Run Process", self.service.resume)

    def step(self) -> None:
        self._run("Step Process", lambda: self.service.step(1))

    def _speed_changed(self, value: float) -> None:
        self._run("Process Speed", lambda: self.service.set_speed(value))

    def _bulk_simulate(self, enabled: bool) -> None:
        self._run("Controller Simulation", lambda: self.service.
                  set_simulation_enabled(self._scope(), enabled))

    def _setup_mode(self) -> None:
        self._run("Setup Mode", lambda: self.service.set_setup_mode(self._scope()))

    def _normal_mode(self) -> None:
        self._run("Normal Mode", lambda: self.service.set_normal_mode(self._scope()))

    def _initialize(self) -> None:
        self._run("Initialize Dynamic Blocks", lambda: self.service.
                  initialize_dynamic_blocks(self._scope()))

    def apply_io_row(self) -> bool:
        index = self.io_table.currentRow()
        source = self.io_table.item(index, 0) if index >= 0 else None
        if source is None:
            return False
        row = source.data(Qt.UserRole)
        try:
            enabled = self.io_table.item(index, 4).checkState() == Qt.Checked
            block = self.service._find_block(row["module"], row["block_id"])
            self.service.set_block_simulation_enabled(
                row["module"], row["block_id"], enabled)
            raw = self.io_table.item(index, 5).text().strip()
            value = raw.lower() in {"true", "on", "1", "yes"} \
                if row["type"] in {"DI", "DO"} else float(raw)
            quality = self.io_table.item(index, 6).text().strip().upper()
            self.service.set_io_value(row["module"], row["block_id"],
                                      value, quality)
            mode = self.io_table.item(index, 7).text().strip()
            if mode and callable(getattr(block, "set_mode", None)):
                self.service.set_mode(row["module"], row["block_id"], mode)
        except Exception as error:  # noqa: BLE001
            if not is_headless():
                QMessageBox.critical(self, "Apply I/O Row", str(error))
            return False
        self.refresh_tables()
        return True

    def _open_signal_simulator(self, checked=False, *, tag=None):
        parent = self.parent()
        opener = getattr(parent, "open_virtual_io_simulator", None)
        if callable(opener):
            dialog = opener()
        else:
            from azeo_control_trainer.azeo_explorer import create_signal_simulator
            dialog = getattr(self, "_signal_simulator", None)
            if dialog is None:
                dialog = create_signal_simulator(self.service.driver, self.project_dir, self)
                self._signal_simulator = dialog
            dialog.show()
            dialog.raise_()
        if tag is not None and callable(getattr(dialog, "focus_signal", None)):
            dialog.focus_signal(tag)
        return dialog

    # --------------------------------------------------------------- snapshots
    def _snapshot_dir(self) -> Path:
        path = self.project_dir / "simulation" / "snapshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _refresh_snapshots(self) -> None:
        rows = self.service.list_snapshots()
        self.snapshot_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            saved = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(row["created_at"]))
            values = (row["name"], saved, row["modules"],
                      "Yes" if row["has_process"] else "No", row["note"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.snapshot_table.setItem(row_index, column, item)

    def save_snapshot(self, path: Path | str | None = None,
                      name: str | None = None, note: str = "") -> Path | None:
        if path is None:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(self, "Save Simulation Snapshot",
                                            "Snapshot name:", text="Checkpoint")
            if not ok or not name.strip():
                return None
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            selected, _ = QFileDialog.getSaveFileName(
                self, "Save Simulation Snapshot",
                str(self._snapshot_dir() / f"{safe}.json"),
                "Azeo Simulation Snapshot (*.json)")
            if not selected:
                return None
            path = selected
        result = self._run("Save Snapshot", lambda: self.service.save_snapshot(
            path, name=name or Path(path).stem, note=note))
        self._refresh_snapshots()
        return result

    def restore_snapshot(self, path: Path | str | None = None) -> bool:
        if path is None:
            row = self.snapshot_table.currentRow()
            item = self.snapshot_table.item(row, 0) if row >= 0 else None
            record = item.data(Qt.UserRole) if item else None
            if not record:
                return False
            path = record["path"]
        if not any((self.restore_operating.isChecked(),
                    self.restore_tuning.isChecked(),
                    self.restore_process.isChecked())):
            return False
        result = self._run("Restore Snapshot", lambda: self.service.restore_snapshot(
            path, operating=self.restore_operating.isChecked(),
            tuning=self.restore_tuning.isChecked(),
            process=self.restore_process.isChecked()))
        return result is not None

    # --------------------------------------------------------------- playback
    def _toggle_recording(self, checked: bool) -> None:
        self.service.recording = checked
        self.record_button.setText("Stop Recording" if checked else "Start Recording")
        self._refresh_playback()

    def add_marker(self, name: str | None = None, note: str = "") -> bool:
        if name is None:
            if is_headless():
                return False
            name, ok = QInputDialog.getText(self, "Playback Marker", "Marker name:")
            if not ok or not name.strip():
                return False
        self.service.add_marker(name, note)
        self._refresh_playback()
        return True

    def _refresh_playback(self) -> None:
        rows = self.service.journal.rows()
        self.playback_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (row["id"], f"{row['sim_time']:.3f}", row["actor"],
                      row["action"], row["target"], _text(row["value"]),
                      " · ".join(value for value in (row["marker"], row["note"])
                                 if value))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self.playback_table.setItem(row_index, column, item)
        if rows and self._playback_position < len(rows):
            self.playback_table.selectRow(self._playback_position)

    def restart_playback(self) -> None:
        self._playback_position = 0
        self._refresh_playback()

    def play_next(self) -> bool:
        rows = self.service.journal.rows()
        while self._playback_position < len(rows):
            event = rows[self._playback_position]
            self._playback_position += 1
            result = self._run("Operator Playback",
                               lambda event=event: self.service.replay_event(event))
            self._refresh_playback()
            if result:
                return True
        return False

    def _refresh_diagnostics(self, diagnostics=None) -> None:
        raw = dict(self.service.diagnostics() if diagnostics is None else diagnostics)
        process = raw.pop("process", {}) or {}
        field_io = raw.pop("field_io", {}) or {}
        rows = [(key.replace("_", " ").title(), _text(value))
                for key, value in raw.items()]
        rows += [(f"Process · {key.replace('_', ' ').title()}", _text(value))
                 for key, value in process.items()
                 if not isinstance(value, (dict, list, tuple))]
        rows += [(f"Field I/O · {key.replace('_', ' ').title()}", _text(value))
                 for key, value in field_io.items()
                 if not isinstance(value, (dict, list, tuple))]
        self.diagnostics_table.setRowCount(len(rows))
        for row_index, (name, value) in enumerate(rows):
            for column, text in ((0, name), (1, value)):
                cell = self.diagnostics_table.item(row_index, column)
                if cell is None:
                    self.diagnostics_table.setItem(row_index, column, QTableWidgetItem(text))
                elif cell.text() != text:
                    cell.setText(text)


__all__ = ["SimulationWorkbenchDialog"]
