"""Controller Diagnostics Dialog — Honeywell C300-style diagnostics.

Detailed diagnostics tabs modeled after Honeywell C300 Controller Detail Display:
  - Main: Overall controller health, uptime, active modules
  - Performance: Scan time statistics, CPU loading analog
  - Blocks: Block type inventory, execution order, status summary
  - I/O: Tag count, store statistics, historian status
  - Memory: Block memory usage estimates, wire counts
  - Network: OPC UA status (if available)

Auto-refreshes at 1 Hz when visible.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    ENGINEERING_QSS, EngineeringMenus, button_command, polish_dialog,
)

import logging
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QProgressBar, QPushButton,
    QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.diagnostics")

# ── ISA-101 Silver styling ──
_DLG_STYLE = ENGINEERING_QSS



def _make_metric_card(label_text: str, value_text: str = "—") -> tuple[QFrame, QLabel]:
    """Create a metric card widget returning (frame, value_label)."""
    frame = QFrame()
    frame.setObjectName("metricCard")
    frame.setFixedHeight(80)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(2)
    val = QLabel(value_text)
    val.setObjectName("metricValue")
    val.setAlignment(Qt.AlignCenter)
    lay.addWidget(val)
    lbl = QLabel(label_text)
    lbl.setObjectName("metricLabel")
    lbl.setAlignment(Qt.AlignCenter)
    lay.addWidget(lbl)
    return frame, val


class ControllerDiagnosticsDialog(QDialog):
    """Honeywell C300-style Controller Diagnostics dialog."""

    def __init__(self, designer_tab, store, parent=None):
        super().__init__(parent)
        self._designer_tab = designer_tab
        self._store = store
        self.setWindowTitle("Controller Diagnostics — CTRL-1")
        self.setMinimumSize(850, 550)
        self.resize(950, 650)
        self.setStyleSheet(_DLG_STYLE)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)

        self._build_ui()
        polish_dialog(self)
        self.menus = EngineeringMenus(self, refresh=self._refresh,
            help_text="View controller health, scan statistics, block inventory and I/O diagnostics. "
            "Select a page from View. Carrier key locked controls the existing controller keylock. "
            "Switchover drills on Performance deliberately interrupt the training controller.")
        self.menus.menu("&Training", [button_command(button, mark="simulator")
            for button in self.findChildren(QPushButton) if "drill" in button.text().lower()])
        for table in self.findChildren(QTableWidget):
            self.menus.table(table, title="Controller diagnostics")
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Controller Diagnostics")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        # The controller's identity — name, model, keylock, DST gauge —
        # is the PK made visible. Text set on every refresh.
        self._lbl_identity = QLabel("")
        self._lbl_identity.setStyleSheet(
            f"color: {UI.blue}; font-size: 9pt; font-weight: bold; "
            "padding-left: 12px;")
        hdr_lay.addWidget(self._lbl_identity)
        hdr_lay.addStretch()
        from PySide6.QtWidgets import QCheckBox

        self._chk_keylock = QCheckBox("Carrier key locked")
        self._chk_keylock.setStyleSheet("font-size: 9pt;")
        self._chk_keylock.toggled.connect(self._set_keylock)
        hdr_lay.addWidget(self._chk_keylock)
        self._lbl_time = QLabel("")
        self._lbl_time.setStyleSheet(f"color: {UI.text_secondary}; font-size: 9pt;")
        hdr_lay.addWidget(self._lbl_time)
        layout.addWidget(hdr)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_main_tab(), "Main")
        self._tabs.addTab(self._build_performance_tab(), "Performance")
        self._tabs.addTab(self._build_blocks_tab(), "Block Types")
        self._tabs.addTab(self._build_io_tab(), "I/O && Tags")
        self._tabs.addTab(self._build_memory_tab(), "Memory")
        layout.addWidget(self._tabs, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ── Tab builders ──

    def _build_main_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        # Top metric cards
        cards = QHBoxLayout()
        cards.setSpacing(8)

        f1, self._val_status = _make_metric_card("Controller Status")
        f2, self._val_uptime = _make_metric_card("Uptime")
        f3, self._val_modules = _make_metric_card("Online Modules")
        f4, self._val_total_scans = _make_metric_card("Total Scans")
        f5, self._val_errors = _make_metric_card("Total Errors")

        for f in (f1, f2, f3, f4, f5):
            cards.addWidget(f)
        lay.addLayout(cards)

        # Module summary table
        lbl = QLabel("Active Modules")
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 4px;")
        lay.addWidget(lbl)

        self._main_table = QTableWidget()
        self._main_table.setColumnCount(7)
        self._main_table.setHorizontalHeaderLabels([
            "Module", "Status", "Blocks", "Scans", "Rate (ms)",
            "Last Scan (ms)", "Online Since",
        ])
        self._main_table.setAlternatingRowColors(True)
        self._main_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._main_table.verticalHeader().setVisible(False)
        hh = self._main_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._main_table, 1)

        return w

    def _build_performance_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        # Scan time metrics
        cards = QHBoxLayout()
        cards.setSpacing(8)
        f1, self._val_last_ms = _make_metric_card("Last Scan (ms)")
        f2, self._val_avg_ms = _make_metric_card("Avg Scan (ms)")
        f3, self._val_max_ms = _make_metric_card("Max Scan (ms)")
        f4, self._val_scan_rate = _make_metric_card("Scan Rate (Hz)")
        for f in (f1, f2, f3, f4):
            cards.addWidget(f)
        lay.addLayout(cards)

        # CPU loading bar
        cpu_frame = QFrame()
        cpu_frame.setObjectName("metricCard")
        cpu_lay = QVBoxLayout(cpu_frame)
        cpu_lay.addWidget(QLabel("CPU Loading (estimated)"))
        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        self._cpu_bar.setValue(0)
        self._cpu_bar.setFixedHeight(24)
        cpu_lay.addWidget(self._cpu_bar)
        self._lbl_cpu = QLabel("")
        self._lbl_cpu.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary};")
        cpu_lay.addWidget(self._lbl_cpu)
        lay.addWidget(cpu_frame)

        # Per-module scan table
        lbl = QLabel("Per-Module Scan Statistics")
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 4px;")
        lay.addWidget(lbl)

        self._perf_table = QTableWidget()
        self._perf_table.setColumnCount(6)
        self._perf_table.setHorizontalHeaderLabels([
            "Module", "Last (ms)", "Avg (ms)", "Max (ms)",
            "Scans", "Errors",
        ])
        self._perf_table.setAlternatingRowColors(True)
        self._perf_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._perf_table.verticalHeader().setVisible(False)
        hh = self._perf_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._perf_table, 1)

        # The switchover drill (PK phase 3): fail the controller on
        # purpose, measure what the process feels. Run it while a trend is
        # open — the report below is the caption for what the trend shows.
        drill_row = QHBoxLayout()
        for label, mode in (("Warm switchover drill", "warm"),
                            ("Cold restart drill", "cold")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, m=mode: self._drill(m))
            drill_row.addWidget(button)
        drill_row.addStretch()
        lay.addLayout(drill_row)
        self._drill_result = QLabel("")
        self._drill_result.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 9pt; "
            "color: #37474F;")
        self._drill_result.setWordWrap(True)
        lay.addWidget(self._drill_result)

        return w

    #: How long the controller stays dead, and how long the settle window
    #: is observed. Long enough to feel, short enough to click twice.
    _DRILL_OUTAGE_MS = 2000
    _DRILL_SAMPLES = 10
    _DRILL_SAMPLE_MS = 300

    def _drill(self, mode: str) -> None:
        from azeo_control_trainer.core.strategy.engine.switchover import (
            SwitchoverDrill,
        )

        get_executive = getattr(self._designer_tab, "controller_executive",
                                None)
        executive = get_executive() if callable(get_executive) else None
        if executive is None:
            self._drill_result.setText("No executive to fail.")
            return
        drill = SwitchoverDrill(self._store, executive)
        if not drill.fail(mode):
            self._drill_result.setText(
                "Nothing is on scan — download modules first.")
            return
        self._drill_result.setText(
            f"{mode.upper()} drill: controller FAILED — standby taking "
            f"over in {self._DRILL_OUTAGE_MS / 1000:.0f} s…")
        QTimer.singleShot(self._DRILL_OUTAGE_MS,
                          lambda: self._drill_takeover(drill))

    def _drill_takeover(self, drill) -> None:
        drill.takeover()
        state = {"left": self._DRILL_SAMPLES}

        def _tick() -> None:
            drill.sample()
            state["left"] -= 1
            if state["left"] > 0:
                QTimer.singleShot(self._DRILL_SAMPLE_MS, _tick)
            else:
                report = drill.report
                self._drill_result.setText(report.text())
                log.info("Switchover drill report: %s", report.text())

        QTimer.singleShot(self._DRILL_SAMPLE_MS, _tick)

    def _build_blocks_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        lbl = QLabel("Block Type Inventory (all online modules)")
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 4px;")
        lay.addWidget(lbl)

        self._blocks_table = QTableWidget()
        self._blocks_table.setColumnCount(4)
        self._blocks_table.setHorizontalHeaderLabels([
            "Block Type", "Category", "Count", "Status",
        ])
        self._blocks_table.setAlternatingRowColors(True)
        self._blocks_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._blocks_table.verticalHeader().setVisible(False)
        self._blocks_table.setSortingEnabled(True)
        hh = self._blocks_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._blocks_table, 1)

        self._lbl_blocks_summary = QLabel("")
        self._lbl_blocks_summary.setStyleSheet(
            f"font-size: 9pt; color: {UI.border}; padding: 2px 6px;")
        lay.addWidget(self._lbl_blocks_summary)

        return w

    def _build_io_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        f1, self._val_tags = _make_metric_card("Store Tags")
        f2, self._val_ai = _make_metric_card("AI Blocks")
        f3, self._val_ao = _make_metric_card("AO Blocks")
        f4, self._val_di = _make_metric_card("DI Blocks")
        f5, self._val_do = _make_metric_card("DO Blocks")
        for f in (f1, f2, f3, f4, f5):
            cards.addWidget(f)
        lay.addLayout(cards)

        lbl = QLabel("I/O Points")
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 4px;")
        lay.addWidget(lbl)

        self._io_table = QTableWidget()
        self._io_table.setColumnCount(5)
        self._io_table.setHorizontalHeaderLabels([
            "Block", "Type", "Tag", "Value", "Status",
        ])
        self._io_table.setAlternatingRowColors(True)
        self._io_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._io_table.verticalHeader().setVisible(False)
        self._io_table.setSortingEnabled(True)
        hh = self._io_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._io_table, 1)

        return w

    def _build_memory_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        f1, self._val_block_mem = _make_metric_card("Block Memory (est)")
        f2, self._val_wire_count = _make_metric_card("Total Wires")
        f3, self._val_fwd_wires = _make_metric_card("Forward Wires")
        f4, self._val_bkcal_wires = _make_metric_card("BKCAL Wires")
        for f in (f1, f2, f3, f4):
            cards.addWidget(f)
        lay.addLayout(cards)

        lbl = QLabel("Memory Breakdown by Module")
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {UI.blue}; padding: 4px;")
        lay.addWidget(lbl)

        self._mem_table = QTableWidget()
        self._mem_table.setColumnCount(5)
        self._mem_table.setHorizontalHeaderLabels([
            "Module", "Blocks", "Wires", "Est. Memory (KB)", "% of Total",
        ])
        self._mem_table.setAlternatingRowColors(True)
        self._mem_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._mem_table.verticalHeader().setVisible(False)
        hh = self._mem_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self._mem_table, 1)

        return w

    # ── Lifecycle ──

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start(1000)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # ── Refresh ──

    def _set_keylock(self, locked: bool) -> None:
        controller = getattr(self._store, "controller", None)
        if controller is not None:
            controller.keylock = bool(locked)

    def _refresh_identity(self) -> None:
        controller = getattr(self._store, "controller", None)
        if controller is None:
            self._lbl_identity.setText("")
            self._chk_keylock.setVisible(False)
            return
        over = controller.over_capacity(self._store)
        self._lbl_identity.setText(controller.identity_line(self._store))
        self._lbl_identity.setStyleSheet(
            "font-size: 9pt; font-weight: bold; padding-left: 12px; "
            + ("color: #C62828;" if over else f"color: {UI.blue};"))
        self._chk_keylock.setVisible(True)
        if self._chk_keylock.isChecked() != controller.keylock:
            self._chk_keylock.blockSignals(True)
            self._chk_keylock.setChecked(controller.keylock)
            self._chk_keylock.blockSignals(False)

    def _refresh(self):
        self._refresh_identity()
        self._lbl_time.setText(datetime.now().strftime("%H:%M:%S"))
        modules = self._gather_modules()
        self._refresh_main(modules)
        self._refresh_performance(modules)
        self._refresh_blocks(modules)
        self._refresh_io(modules)
        self._refresh_memory(modules)

    def _gather_modules(self) -> list[tuple]:
        """Gather (name, canvas, status, compiled) for online canvases."""
        tab = self._designer_tab
        if tab is None:
            return []
        from ..designer_tab import StrategyCanvas
        result = []
        for i in range(tab._canvas_tabs.count()):
            canvas = tab._canvas_tabs.widget(i)
            if not isinstance(canvas, StrategyCanvas):
                continue
            if canvas.runtime and canvas.runtime.is_online:
                name = tab._canvas_tabs.tabText(i).replace(" *", "").strip()
                status = canvas.runtime.get_status()
                compiled = canvas.runtime.compiled
                result.append((name, canvas, status, compiled))
        return result

    def _refresh_main(self, modules):
        n = len(modules)
        self._val_status.setText("RUNNING" if n > 0 else "IDLE")
        self._val_status.setStyleSheet(
            f"font-size: 18pt; font-weight: bold; "
            f"color: {'#2E7D32' if n > 0 else f'{UI.text_muted}'};")
        self._val_modules.setText(str(n))

        total_scans = sum(s.get("scan_count", 0) for _, _, s, _ in modules)
        total_errors = sum(s.get("error_count", 0) for _, _, s, _ in modules)
        self._val_total_scans.setText(f"{total_scans:,}")
        self._val_errors.setText(str(total_errors))
        self._val_errors.setStyleSheet(
            f"font-size: 18pt; font-weight: bold; "
            f"color: {'#C62828' if total_errors > 0 else '#2E7D32'};")

        # Uptime
        if modules:
            max_dur = max(s.get("online_duration", 0) for _, _, s, _ in modules)
            td = timedelta(seconds=int(max_dur))
            self._val_uptime.setText(str(td))
        else:
            self._val_uptime.setText("—")

        # Module table
        self._main_table.setRowCount(n)
        for r, (name, canvas, status, compiled) in enumerate(modules):
            self._main_table.setItem(r, 0, QTableWidgetItem(name))
            st = "ACTIVE" if status["online"] else "INACTIVE"
            item = QTableWidgetItem(st)
            item.setForeground(QColor("#2E7D32" if st == "ACTIVE" else "#78909C"))
            self._main_table.setItem(r, 1, item)
            self._main_table.setItem(r, 2, QTableWidgetItem(
                str(status.get("block_count", 0))))
            self._main_table.setItem(r, 3, QTableWidgetItem(
                f"{status.get('scan_count', 0):,}"))
            graph = getattr(getattr(canvas.runtime, "compiled", None),
                            "graph", None)
            rate = getattr(graph, "scan_ms", 500)
            overruns = getattr(canvas.runtime, "_pk_overruns", 0)
            rate_item = QTableWidgetItem(
                f"{rate}" + (f"  ({overruns} overrun)" if overruns else ""))
            if overruns:
                rate_item.setForeground(QColor("#C62828"))
            self._main_table.setItem(r, 4, rate_item)
            self._main_table.setItem(r, 5, QTableWidgetItem(
                f"{status.get('last_scan_ms', 0):.2f}"))
            dur = status.get("online_duration", 0)
            online_since = datetime.now() - timedelta(seconds=dur)
            self._main_table.setItem(r, 6, QTableWidgetItem(
                online_since.strftime("%H:%M:%S")))

    def _refresh_performance(self, modules):
        if not modules:
            for v in (self._val_last_ms, self._val_avg_ms,
                      self._val_max_ms, self._val_scan_rate):
                v.setText("—")
            self._cpu_bar.setValue(0)
            self._perf_table.setRowCount(0)
            return

        # Aggregate stats
        last_ms = max(s.get("last_scan_ms", 0) for _, _, s, _ in modules)
        avg_ms = (sum(s.get("avg_scan_ms", 0) for _, _, s, _ in modules)
                  / len(modules)) if modules else 0
        max_ms = max(s.get("max_scan_ms", 0) for _, _, s, _ in modules)

        self._val_last_ms.setText(f"{last_ms:.2f}")
        self._val_avg_ms.setText(f"{avg_ms:.2f}")
        self._val_max_ms.setText(f"{max_ms:.2f}")

        # The scan rate and period come from the executive that actually
        # ticks the modules. The old numbers were both invented: rate was
        # 1000/avg-scan-cost (a 0.1 ms scan does not run at 10 000 scans/s)
        # and the period was hardcoded 1000 ms while the executive default
        # is 500 — so the loading figure lied by a factor of two.
        executive = None
        get_executive = getattr(self._designer_tab, "controller_executive",
                                None)
        if callable(get_executive):
            try:
                executive = get_executive()
            except Exception:                              # noqa: BLE001
                executive = None
        scan_period_ms = float(getattr(executive, "period_ms", 0) or 1000.0)
        rate = 1000.0 / scan_period_ms if scan_period_ms > 0 else 0.0
        self._val_scan_rate.setText(f"{rate:.1f}")

        cpu_pct = min(100, int(avg_ms / scan_period_ms * 100))
        self._cpu_bar.setValue(cpu_pct)
        overruns = int(getattr(executive, "overrun_count", 0) or 0)
        self._lbl_cpu.setText(
            f"Using {avg_ms:.2f} ms of {scan_period_ms:.0f} ms scan period "
            f"({cpu_pct}%) — {overruns} overrun(s)")

        # Per-module table
        self._perf_table.setRowCount(len(modules))
        for r, (name, _, status, _) in enumerate(modules):
            self._perf_table.setItem(r, 0, QTableWidgetItem(name))
            self._perf_table.setItem(r, 1, QTableWidgetItem(
                f"{status.get('last_scan_ms', 0):.2f}"))
            self._perf_table.setItem(r, 2, QTableWidgetItem(
                f"{status.get('avg_scan_ms', 0):.2f}"))
            self._perf_table.setItem(r, 3, QTableWidgetItem(
                f"{status.get('max_scan_ms', 0):.2f}"))
            self._perf_table.setItem(r, 4, QTableWidgetItem(
                f"{status.get('scan_count', 0):,}"))
            err_item = QTableWidgetItem(str(status.get("error_count", 0)))
            if status.get("error_count", 0) > 0:
                err_item.setForeground(QColor("#C62828"))
            self._perf_table.setItem(r, 5, err_item)

    def _refresh_blocks(self, modules):
        # Count blocks by type
        type_counts = {}
        type_cats = {}
        type_statuses = {}
        for _, _, _, compiled in modules:
            if not compiled:
                continue
            for bid in compiled.exec_order:
                block = compiled.graph.blocks.get(bid)
                if block:
                    bt = block.block_type
                    type_counts[bt] = type_counts.get(bt, 0) + 1
                    cat = getattr(block, 'category', None)
                    type_cats[bt] = cat.value if cat else "—"
                    st = getattr(block, 'status', None)
                    if st and st.value != "GOOD":
                        type_statuses[bt] = st.value
                    elif bt not in type_statuses:
                        type_statuses[bt] = "GOOD"

        self._blocks_table.setSortingEnabled(False)
        self._blocks_table.setRowCount(len(type_counts))
        for r, (bt, count) in enumerate(sorted(type_counts.items())):
            self._blocks_table.setItem(r, 0, QTableWidgetItem(bt))
            self._blocks_table.setItem(r, 1, QTableWidgetItem(
                type_cats.get(bt, "—")))
            self._blocks_table.setItem(r, 2, QTableWidgetItem(str(count)))
            st = type_statuses.get(bt, "GOOD")
            st_item = QTableWidgetItem(st)
            st_item.setForeground(QColor(
                "#2E7D32" if st == "GOOD" else "#C62828"))
            self._blocks_table.setItem(r, 3, st_item)
        self._blocks_table.setSortingEnabled(True)

        total = sum(type_counts.values())
        types = len(type_counts)
        self._lbl_blocks_summary.setText(
            f"{total} blocks across {types} types")

    def _refresh_io(self, modules):
        # Count I/O blocks and populate table
        io_rows = []
        n_ai = n_ao = n_di = n_do = 0
        for _, _, _, compiled in modules:
            if not compiled:
                continue
            for bid in compiled.exec_order:
                block = compiled.graph.blocks.get(bid)
                if not block:
                    continue
                bt = block.block_type
                if bt in ("AI", "AO", "DI", "DO"):
                    tag = block.config.params.get("tag", "")
                    val = ""
                    status = "GOOD"
                    if self._store and tag:
                        v = self._store.get(tag)
                        val = f"{v:.2f}" if isinstance(v, float) else str(v) if v is not None else ""
                    io_rows.append((block.instance_name, bt, tag, val, status))
                    if bt == "AI":
                        n_ai += 1
                    elif bt == "AO":
                        n_ao += 1
                    elif bt == "DI":
                        n_di += 1
                    else:
                        n_do += 1

        self._val_ai.setText(str(n_ai))
        self._val_ao.setText(str(n_ao))
        self._val_di.setText(str(n_di))
        self._val_do.setText(str(n_do))

        # Tag count
        if self._store and hasattr(self._store, 'get_all'):
            try:
                all_tags = self._store.get_all()
                self._val_tags.setText(str(len(all_tags)))
            except Exception:
                self._val_tags.setText("?")
        else:
            self._val_tags.setText("—")

        self._io_table.setSortingEnabled(False)
        self._io_table.setRowCount(len(io_rows))
        for r, (name, bt, tag, val, status) in enumerate(io_rows):
            self._io_table.setItem(r, 0, QTableWidgetItem(name))
            self._io_table.setItem(r, 1, QTableWidgetItem(bt))
            self._io_table.setItem(r, 2, QTableWidgetItem(tag))
            self._io_table.setItem(r, 3, QTableWidgetItem(val))
            self._io_table.setItem(r, 4, QTableWidgetItem(status))
        self._io_table.setSortingEnabled(True)

    def _refresh_memory(self, modules):
        total_blocks = 0
        total_fwd = 0
        total_bkcal = 0
        mem_rows = []

        for name, _, status, compiled in modules:
            if not compiled:
                continue
            n_blocks = len(compiled.exec_order)
            n_fwd = len(compiled.forward_wires)
            n_bkcal = len(compiled.bkcal_wires)
            total_blocks += n_blocks
            total_fwd += n_fwd
            total_bkcal += n_bkcal
            # Rough memory estimate: ~2KB per block + ~0.5KB per wire
            est_kb = n_blocks * 2 + (n_fwd + n_bkcal) * 0.5
            mem_rows.append((name, n_blocks, n_fwd + n_bkcal, est_kb))

        total_wires = total_fwd + total_bkcal
        total_mem = sum(r[3] for r in mem_rows)

        self._val_block_mem.setText(f"{total_mem:.0f} KB")
        self._val_wire_count.setText(str(total_wires))
        self._val_fwd_wires.setText(str(total_fwd))
        self._val_bkcal_wires.setText(str(total_bkcal))

        self._mem_table.setRowCount(len(mem_rows))
        for r, (name, blocks, wires, est_kb) in enumerate(mem_rows):
            self._mem_table.setItem(r, 0, QTableWidgetItem(name))
            self._mem_table.setItem(r, 1, QTableWidgetItem(str(blocks)))
            self._mem_table.setItem(r, 2, QTableWidgetItem(str(wires)))
            self._mem_table.setItem(r, 3, QTableWidgetItem(f"{est_kb:.1f}"))
            pct = (est_kb / total_mem * 100) if total_mem > 0 else 0
            self._mem_table.setItem(r, 4, QTableWidgetItem(f"{pct:.0f}%"))
