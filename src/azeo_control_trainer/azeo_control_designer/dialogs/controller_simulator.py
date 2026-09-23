"""Controller Simulator — Offline simulation mode for strategy testing.

Honeywell-style controller simulator that allows:
  - Running strategies offline without the live process simulation
  - Manual input injection (set AI values manually)
  - Step response testing (apply step changes and observe PID behavior)
  - Strategy validation before downloading to the "real" controller
  - Standalone scan execution with configurable dt

This creates a sandboxed SharedDataStore and runs the strategy runtime
independently from the main simulation engine.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    Command, ENGINEERING_QSS, EngineeringMenus, button_command, polish_dialog,
)

import logging
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.simulator")

_DLG_STYLE = ENGINEERING_QSS






class _SandboxStore:
    """Minimal SharedDataStore for offline simulation."""

    def __init__(self):
        self._data: dict = {}
        self._writes: deque = deque(maxlen=1000)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def get_all(self):
        return dict(self._data)

    def set(self, key, value):
        self._data[key] = value

    def set_many(self, updates: dict):
        self._data.update(updates)

    def queue_write(self, key, value):
        self._writes.append((key, value))
        self._data[key] = value

    def drain_writes(self):
        writes = list(self._writes)
        self._writes.clear()
        return writes


class ControllerSimulatorDialog(QDialog):
    """Offline controller simulator for strategy testing."""

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._sandbox = _SandboxStore()
        self._runtime = None
        self._bridge = None
        self._running = False
        self._scan_count = 0
        self._scan_history: list[dict] = []  # last N scan results
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._execute_scan)

        self.setWindowTitle("Controller Simulator — Offline Mode")
        self.setMinimumSize(950, 600)
        self.resize(1050, 700)
        self.setStyleSheet(_DLG_STYLE)

        self._build_ui()
        self._initialize()
        polish_dialog(self)
        self.menus = EngineeringMenus(self, refresh=self._refresh_outputs,
            help_text="Compile the current strategy into an offline sandbox. Inject input values, then Run or Single Step. "
            "Stop pauses scans. Step Test applies the configured step in the sandbox. This window does not operate live field I/O.")
        self.menus.menu("&Simulation", [button_command(button, mark=mark) for button, mark in (
            (self._btn_compile, "compile"), (self._btn_run, "connect"),
            (self._btn_stop, "disconnect"), (self._btn_step, "exec_order"))])
        self.menus.table(self._input_table, lambda row: [Command("Edit injected value",
            lambda: self._input_table.editItem(self._input_table.item(row, 4)), "params")], title="Sandbox input")
        self.menus.table(self._output_table, title="Sandbox outputs")
        self.menus.table(self._step_table, title="Step test results")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Controller Simulator (Offline)")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()
        self._lbl_mode = QLabel("OFFLINE")
        self._lbl_mode.setStyleSheet(
            f"font-size: 10pt; font-weight: bold; color: {UI.blue};")
        hdr_lay.addWidget(self._lbl_mode)
        layout.addWidget(hdr)

        # Control bar
        ctrl_grp = QGroupBox("Simulation Control")
        ctrl_lay = QHBoxLayout(ctrl_grp)

        self._btn_compile = QPushButton("Compile")
        self._btn_compile.clicked.connect(self._compile)
        ctrl_lay.addWidget(self._btn_compile)

        self._btn_run = QPushButton("Run")
        self._btn_run.clicked.connect(self._start_running)
        ctrl_lay.addWidget(self._btn_run)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_running)
        ctrl_lay.addWidget(self._btn_stop)

        self._btn_step = QPushButton("Single Step")
        self._btn_step.clicked.connect(self._single_step)
        ctrl_lay.addWidget(self._btn_step)

        ctrl_lay.addWidget(QLabel("dt:"))
        self._spn_dt = QDoubleSpinBox()
        self._spn_dt.setRange(0.001, 10.0)
        self._spn_dt.setValue(1.0)
        self._spn_dt.setDecimals(3)
        self._spn_dt.setSuffix(" s")
        self._spn_dt.setFixedWidth(130)
        ctrl_lay.addWidget(self._spn_dt)

        ctrl_lay.addWidget(QLabel("Rate:"))
        self._spn_rate = QSpinBox()
        self._spn_rate.setRange(1, 100)
        self._spn_rate.setValue(1)
        self._spn_rate.setSuffix(" Hz")
        self._spn_rate.setFixedWidth(110)
        ctrl_lay.addWidget(self._spn_rate)

        ctrl_lay.addStretch()

        self._lbl_scans = QLabel("Scans: 0")
        self._lbl_scans.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue};")
        ctrl_lay.addWidget(self._lbl_scans)

        layout.addWidget(ctrl_grp)

        # Main tabs
        tabs = QTabWidget()
        tabs.addTab(self._build_inputs_tab(), "Input Injection")
        tabs.addTab(self._build_outputs_tab(), "Block Outputs")
        tabs.addTab(self._build_step_test_tab(), "Step Test")
        layout.addWidget(tabs, 1)

        # Bottom
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_reset = QPushButton("Reset Simulator")
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_reset)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _build_inputs_tab(self) -> QWidget:
        """Manual input injection table."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)

        info = QLabel(
            "Set AI/DI input values manually. These values will be used by "
            "the strategy instead of live process data.")
        info.setWordWrap(True)
        info.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary}; padding: 4px;")
        lay.addWidget(info)

        self._input_table = QTableWidget()
        self._input_table.setColumnCount(5)
        self._input_table.setHorizontalHeaderLabels([
            "Block", "Type", "Tag", "Current Value", "Inject Value",
        ])
        self._input_table.setAlternatingRowColors(True)
        self._input_table.verticalHeader().setVisible(False)
        self._input_table.verticalHeader().setDefaultSectionSize(24)
        hh = self._input_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._input_table, 1)

        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply Injected Values")
        btn_apply.clicked.connect(self._apply_inputs)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        return w

    def _build_outputs_tab(self) -> QWidget:
        """Block outputs display."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)

        self._output_table = QTableWidget()
        self._output_table.setColumnCount(7)
        self._output_table.setHorizontalHeaderLabels([
            "Block", "Type", "PV", "SP", "OUT", "Mode", "Status",
        ])
        self._output_table.setAlternatingRowColors(True)
        self._output_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._output_table.verticalHeader().setVisible(False)
        self._output_table.verticalHeader().setDefaultSectionSize(22)
        self._output_table.setSortingEnabled(True)
        hh = self._output_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 7):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._output_table, 1)

        return w

    def _build_step_test_tab(self) -> QWidget:
        """Step test configuration."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        step_grp = QGroupBox("Step Test Configuration")
        step_lay = QGridLayout(step_grp)

        self._txt_step_tag = QLineEdit()
        self._txt_step_tag.setPlaceholderText("e.g. xmeas_9")

        self._spn_step_from = QDoubleSpinBox()
        self._spn_step_from.setRange(-1e6, 1e6)
        self._spn_step_from.setValue(120.0)
        self._spn_step_from.setDecimals(2)

        self._spn_step_to = QDoubleSpinBox()
        self._spn_step_to.setRange(-1e6, 1e6)
        self._spn_step_to.setValue(125.0)
        self._spn_step_to.setDecimals(2)

        self._spn_step_scans = QSpinBox()
        self._spn_step_scans.setRange(10, 10000)
        self._spn_step_scans.setValue(500)
        # Labels above the fields leave room for precision and the modern
        # step controls; the former fixed widths clipped 500 to a single digit.
        for column, (label, field) in enumerate((
            ("Tag", self._txt_step_tag), ("From", self._spn_step_from),
            ("To", self._spn_step_to), ("Scans", self._spn_step_scans),
        )):
            step_lay.addWidget(QLabel(label), 0, column)
            step_lay.addWidget(field, 1, column)
            step_lay.setColumnStretch(column, 1)

        btn_run_step = QPushButton("Run Step Test")
        btn_run_step.clicked.connect(self._run_step_test)
        step_lay.addWidget(btn_run_step, 1, 4)

        lay.addWidget(step_grp)

        # Results table
        self._step_table = QTableWidget()
        self._step_table.setColumnCount(5)
        self._step_table.setHorizontalHeaderLabels([
            "Scan", "Time (s)", "PV", "SP", "OUT",
        ])
        self._step_table.setAlternatingRowColors(True)
        self._step_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._step_table.verticalHeader().setVisible(False)
        hh = self._step_table.horizontalHeader()
        for i in range(5):
            hh.setSectionResizeMode(i, QHeaderView.Stretch)
        lay.addWidget(self._step_table, 1)

        return w

    # ── Initialization ──

    def _initialize(self):
        """Set up the sandbox with current strategy blocks."""
        graph = self._canvas.scene.graph
        if not graph.blocks:
            return

        # Populate input table with AI/DI blocks
        io_blocks = []
        for block in graph.blocks.values():
            if block.block_type in ("AI", "AO", "DI", "DO"):
                tag = block.config.params.get("tag", "")
                io_blocks.append((block.instance_name, block.block_type, tag))

        self._input_table.setRowCount(len(io_blocks))
        for r, (name, bt, tag) in enumerate(io_blocks):
            self._input_table.setItem(r, 0, QTableWidgetItem(name))
            self._input_table.setItem(r, 1, QTableWidgetItem(bt))
            self._input_table.setItem(r, 2, QTableWidgetItem(tag))
            self._input_table.setItem(r, 3, QTableWidgetItem("0.0"))
            # Editable inject value
            inject = QTableWidgetItem("0.0")
            inject.setFlags(inject.flags() | Qt.ItemIsEditable)
            inject.setBackground(QColor(UI.selection))
            self._input_table.setItem(r, 4, inject)

    # ── Compile ──

    def _compile(self):
        """Compile the strategy for offline simulation."""
        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
        from azeo_control_trainer.core.strategy.engine.bridge import DataBridge

        graph = self._canvas.scene.graph
        if not graph.blocks:
            QMessageBox.warning(self, "Compile", "Strategy has no blocks.")
            return

        try:
            compiled = compile_strategy(graph)
        except CompileError as e:
            QMessageBox.critical(self, "Compile Error", str(e))
            return

        # Create sandbox bridge and runtime
        self._sandbox = _SandboxStore()
        self._bridge = DataBridge(self._sandbox)
        self._runtime = StrategyRuntime()
        self._runtime.load(compiled, self._bridge)

        # Initialize outputs in sandbox
        self._apply_inputs()
        self._runtime.go_online()
        self._scan_count = 0

        QMessageBox.information(
            self, "Compile",
            f"Strategy compiled for offline simulation.\n"
            f"Blocks: {len(compiled.exec_order)}\n"
            f"Forward wires: {len(compiled.forward_wires)}\n"
            f"BKCAL wires: {len(compiled.bkcal_wires)}")

        self._btn_run.setEnabled(True)
        self._btn_step.setEnabled(True)
        self._refresh_outputs()

    # ── Run control ──

    def _start_running(self):
        if not self._runtime or not self._runtime.is_online:
            QMessageBox.warning(self, "Run", "Compile first.")
            return
        self._running = True
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_step.setEnabled(False)
        rate = self._spn_rate.value()
        self._timer.start(int(1000 / rate))
        self._lbl_mode.setText("RUNNING")
        self._lbl_mode.setStyleSheet(
            "font-size: 10pt; font-weight: bold; color: #2E7D32;")

    def _stop_running(self):
        self._running = False
        self._timer.stop()
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_step.setEnabled(True)
        self._lbl_mode.setText("STOPPED")
        self._lbl_mode.setStyleSheet(
            "font-size: 10pt; font-weight: bold; color: #C62828;")
        self._refresh_outputs()

    def _single_step(self):
        if not self._runtime or not self._runtime.is_online:
            QMessageBox.warning(self, "Step", "Compile first.")
            return
        self._execute_scan()
        self._refresh_outputs()

    def _execute_scan(self):
        """Execute one scan cycle."""
        if not self._runtime:
            return
        dt = self._spn_dt.value()
        self._runtime.execute_scan(dt)
        self._scan_count += 1
        self._lbl_scans.setText(f"Scans: {self._scan_count}")

        # Update outputs periodically during run
        if self._running and self._scan_count % 10 == 0:
            self._refresh_outputs()

    def _apply_inputs(self):
        """Write injected input values to the sandbox store."""
        for r in range(self._input_table.rowCount()):
            tag_item = self._input_table.item(r, 2)
            inject_item = self._input_table.item(r, 4)
            if tag_item and inject_item and tag_item.text():
                try:
                    val = float(inject_item.text())
                    self._sandbox.set(tag_item.text(), val)
                    # Update current value display
                    self._input_table.setItem(
                        r, 3, QTableWidgetItem(f"{val:.4f}"))
                except ValueError:
                    pass

    def _refresh_outputs(self):
        """Refresh the block outputs table."""
        if not self._runtime or not self._runtime.compiled:
            return

        graph = self._runtime.compiled.graph
        store = self._sandbox
        rows = []

        for block_id in self._runtime.compiled.exec_order:
            block = graph.blocks.get(block_id)
            if not block:
                continue

            bt = block.block_type
            pv = sp = out = mode = ""
            status = block.status.value if hasattr(block, 'status') else "GOOD"

            if bt == "PID":
                iname = block.instance_name
                pv = self._fmt(store.get(f"ctrl.{iname}.PV"))
                sp = self._fmt(store.get(f"ctrl.{iname}.SP"))
                out = self._fmt(store.get(f"ctrl.{iname}.OUT"))
                m = store.get(f"ctrl.{iname}.MODE")
                mode = str(m) if m else ""
            elif bt == "AI":
                tag = block.config.params.get("tag", "")
                pv = self._fmt(store.get(tag)) if tag else ""
            elif bt == "AO":
                tag = block.config.params.get("tag", "")
                out = self._fmt(store.get(tag)) if tag else ""
            else:
                for tname, term in block.outputs.items():
                    if tname not in ("BKCAL_OUT", "MODE"):
                        out = self._fmt(term.value)
                        break

            rows.append((block.instance_name, bt, pv, sp, out, mode, status))

        self._output_table.setSortingEnabled(False)
        self._output_table.setRowCount(len(rows))
        for r, (name, bt, pv, sp, out, mode, status) in enumerate(rows):
            self._output_table.setItem(r, 0, QTableWidgetItem(name))
            self._output_table.setItem(r, 1, QTableWidgetItem(bt))
            self._output_table.setItem(r, 2, QTableWidgetItem(pv))
            self._output_table.setItem(r, 3, QTableWidgetItem(sp))
            self._output_table.setItem(r, 4, QTableWidgetItem(out))
            self._output_table.setItem(r, 5, QTableWidgetItem(mode))
            st_item = QTableWidgetItem(status)
            st_item.setForeground(QColor(
                "#2E7D32" if status == "GOOD" else "#C62828"))
            self._output_table.setItem(r, 6, st_item)
        self._output_table.setSortingEnabled(True)

    def _run_step_test(self):
        """Run a step test: apply step change and execute N scans."""
        if not self._runtime or not self._runtime.is_online:
            QMessageBox.warning(self, "Step Test", "Compile first.")
            return

        tag = self._txt_step_tag.text().strip()
        if not tag:
            QMessageBox.warning(self, "Step Test", "Enter a tag name.")
            return

        from_val = self._spn_step_from.value()
        to_val = self._spn_step_to.value()
        n_scans = self._spn_step_scans.value()
        dt = self._spn_dt.value()

        # Set initial value and run a few scans to stabilize
        self._sandbox.set(tag, from_val)
        for _ in range(50):
            self._runtime.execute_scan(dt)

        # Apply step change
        self._sandbox.set(tag, to_val)

        # Collect response data
        results = []
        # Find the PID block that reads this tag
        pid_name = None
        graph = self._runtime.compiled.graph
        for block in graph.blocks.values():
            if block.block_type == "PID":
                # Check if any AI feeding this PID uses our tag
                pid_name = block.instance_name
                break

        for i in range(n_scans):
            self._runtime.execute_scan(dt)
            self._scan_count += 1

            pv_val = self._sandbox.get(tag, 0.0)
            sp_val = ""
            out_val = ""
            if pid_name:
                sp_val = self._sandbox.get(f"ctrl.{pid_name}.SP", "")
                out_val = self._sandbox.get(f"ctrl.{pid_name}.OUT", "")

            results.append((i + 1, (i + 1) * dt, pv_val, sp_val, out_val))

        # Display results
        self._step_table.setRowCount(len(results))
        for r, (scan, t, pv, sp, out) in enumerate(results):
            self._step_table.setItem(r, 0, QTableWidgetItem(str(scan)))
            self._step_table.setItem(r, 1, QTableWidgetItem(f"{t:.2f}"))
            self._step_table.setItem(r, 2, QTableWidgetItem(self._fmt(pv)))
            self._step_table.setItem(r, 3, QTableWidgetItem(self._fmt(sp)))
            self._step_table.setItem(r, 4, QTableWidgetItem(self._fmt(out)))

        self._lbl_scans.setText(f"Scans: {self._scan_count}")
        self._refresh_outputs()

        QMessageBox.information(
            self, "Step Test Complete",
            f"Ran {n_scans} scans with dt={dt}s.\n"
            f"Step: {tag} from {from_val} to {to_val}")

    def _reset(self):
        """Reset the azeo_control_trainer."""
        self._stop_running()
        self._sandbox = _SandboxStore()
        self._runtime = None
        self._bridge = None
        self._scan_count = 0
        self._lbl_scans.setText("Scans: 0")
        self._lbl_mode.setText("OFFLINE")
        self._lbl_mode.setStyleSheet(
            f"font-size: 10pt; font-weight: bold; color: {UI.blue};")
        self._output_table.setRowCount(0)
        self._step_table.setRowCount(0)
        self._initialize()

    def closeEvent(self, event):
        self._stop_running()
        event.accept()

    @staticmethod
    def _fmt(val) -> str:
        if val is None or val == "":
            return ""
        if isinstance(val, float):
            return f"{val:.4f}"
        return str(val)
