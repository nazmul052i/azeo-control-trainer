"""Modeless online FBD debugger for one Control Designer module canvas."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import weakref

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class RuntimeDebuggerDialog(QDialog):
    """Inspect and control block-boundary execution without going offline."""

    BLOCK_ID_ROLE = Qt.UserRole + 1

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        # The canvas owns this modeless dialog.  Holding it strongly here
        # creates canvas -> dialog -> canvas and leaves a Python wrapper alive
        # after Qt destroys the window.  A weak reference also lets a tab close
        # cleanly while the debugger is open.
        self._canvas_ref = weakref.ref(canvas)
        self._updating = False
        self.setWindowTitle("Online FBD Debugger")
        self.setMinimumSize(720, 460)
        self.resize(820, 540)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @property
    def runtime(self):
        canvas = self._canvas_ref()
        return getattr(canvas, "runtime", None) if canvas is not None else None

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("ONLINE FBD DEBUGGER")
        title.setStyleSheet(
            "font-size: 11pt; font-weight: 700; color: #173A5E;")
        header.addWidget(title)
        header.addStretch()
        self._state = QLabel("OFFLINE")
        self._state.setAlignment(Qt.AlignCenter)
        self._state.setMinimumWidth(110)
        header.addWidget(self._state)
        root.addLayout(header)

        self._position = QLabel("Current: -    Next: -")
        self._position.setStyleSheet(
            "background: #F4F6F8; border: 1px solid #C4CCD4; "
            f"padding: 6px 8px; color: {UI.text};")
        root.addWidget(self._position)

        controls = QHBoxLayout()
        self._pause_resume = QPushButton("Pause")
        self._pause_resume.clicked.connect(self._toggle_pause)
        controls.addWidget(self._pause_resume)
        self._step = QPushButton("Step Block")
        self._step.clicked.connect(self._step_block)
        controls.addWidget(self._step)
        self._scan = QPushButton("Run Scan")
        self._scan.clicked.connect(self._run_scan)
        controls.addWidget(self._scan)
        self._run_to = QPushButton("Run to Selected")
        self._run_to.clicked.connect(self._run_to_selected)
        controls.addWidget(self._run_to)
        self._abort = QPushButton("Abort Partial Scan")
        self._abort.clicked.connect(self._abort_scan)
        controls.addWidget(self._abort)
        controls.addStretch()
        self._clear = QPushButton("Clear Breakpoints")
        self._clear.clicked.connect(self._clear_breakpoints)
        controls.addWidget(self._clear)
        root.addLayout(controls)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Break", "Order", "Block", "Type", "State"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.itemChanged.connect(self._breakpoint_changed)
        self._table.itemDoubleClicked.connect(self._activate_block)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self._table)

        terminal_panel = QWidget()
        terminal_layout = QVBoxLayout(terminal_panel)
        terminal_layout.setContentsMargins(0, 4, 0, 0)
        terminal_layout.setSpacing(4)
        self._terminal_title = QLabel("TERMINALS — Select a block")
        self._terminal_title.setStyleSheet(
            "color: #173A5E; font-size: 9pt; font-weight: 700;")
        terminal_layout.addWidget(self._terminal_title)
        self._terminals = QTableWidget(0, 6)
        self._terminals.setHorizontalHeaderLabels(
            ["Direction", "Terminal", "Live value", "Quality", "Limit", "Forced"])
        self._terminals.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._terminals.setSelectionMode(QAbstractItemView.SingleSelection)
        self._terminals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._terminals.setAlternatingRowColors(True)
        self._terminals.verticalHeader().setVisible(False)
        terminal_header = self._terminals.horizontalHeader()
        terminal_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        terminal_header.setSectionResizeMode(1, QHeaderView.Stretch)
        terminal_header.setSectionResizeMode(2, QHeaderView.Stretch)
        terminal_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        terminal_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        terminal_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        terminal_layout.addWidget(self._terminals, 1)
        splitter.addWidget(terminal_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 190])
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self._detail = QLabel(
            "Pause keeps the module online. Step and Run Scan preserve the "
            "compiled scan's forward-wire and BKCAL boundaries.")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #586674; font-size: 9pt;")
        footer.addWidget(self._detail, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        self.setStyleSheet("""
            QDialog { background: #E8EBEF; }
            QPushButton {
                background: #F7F8FA; color: #20364A;
                border: 1px solid #AEB8C2; border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover { background: #E0EAF3; }
            QPushButton:disabled { color: #96A0AA; background: #E4E7EA; }
            QTableWidget {
                background: #FFFFFF; alternate-background-color: #F5F7F9;
                border: 1px solid #AEB8C2; gridline-color: #D9DEE3;
            }
            QHeaderView::section {
                background: #D7DDE3; color: #20364A; font-weight: 600;
                border: none; border-right: 1px solid #BEC6CE;
                border-bottom: 1px solid #AEB8C2; padding: 4px;
            }
        """)

    @staticmethod
    def _block_label(block: dict | None) -> str:
        if not block:
            return "-"
        return f"{block['order']}: {block['name']} ({block['type']})"

    def refresh(self):
        runtime = self.runtime
        if runtime is None or runtime.compiled is None:
            self._render_unavailable("NOT DOWNLOADED")
            return
        status = runtime.get_debug_status()
        online = status["online"]
        paused = status["paused"]
        if not online:
            state_text, state_bg = "OFFLINE", "#777F87"
        elif paused:
            state_text, state_bg = "PAUSED", "#D17A00"
        else:
            state_text, state_bg = "RUNNING", "#168247"
        self._state.setText(state_text)
        self._state.setStyleSheet(
            f"background: {state_bg}; color: white; font-weight: 700; "
            "border-radius: 3px; padding: 4px 10px;")
        self._position.setText(
            f"Current: {self._block_label(status['current_block'])}    "
            f"Next: {self._block_label(status['next_block'])}    "
            f"Reason: {status['pause_reason'].replace('_', ' ')}")
        self._rebuild_rows(status)
        self._refresh_terminal_rows()
        self._update_controls()

    def _render_unavailable(self, message: str):
        self._state.setText(message)
        self._state.setStyleSheet(
            "background: #777F87; color: white; font-weight: 700; "
            "border-radius: 3px; padding: 4px 10px;")
        self._position.setText("Current: -    Next: -")
        self._table.setRowCount(0)
        self._terminal_title.setText("TERMINALS — Select a block")
        self._terminals.setRowCount(0)
        self._update_controls()

    def _rebuild_rows(self, status: dict):
        runtime = self.runtime
        compiled = runtime.compiled
        selected = self._selected_block_id()
        current_id = (status["current_block"] or {}).get("id")
        next_id = (status["next_block"] or {}).get("id")
        breakpoints = set(status["breakpoints"])
        needs_rebuild = self._table.rowCount() != len(compiled.exec_order)
        if not needs_rebuild:
            row_ids = {
                self._table.item(row, 0).data(self.BLOCK_ID_ROLE)
                for row in range(self._table.rowCount())
            }
            needs_rebuild = row_ids != set(compiled.exec_order)
        target_id = selected if selected in compiled.exec_order else (
            current_id or next_id or (
                compiled.exec_order[0] if compiled.exec_order else None
            )
        )
        target_row = -1
        self._updating = True
        try:
            if needs_rebuild:
                self._table.setRowCount(len(compiled.exec_order))
            for row, block_id in enumerate(compiled.exec_order):
                block = compiled.graph.blocks[block_id]
                check = self._table.item(row, 0)
                if check is None:
                    check = QTableWidgetItem()
                    check.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable
                        | Qt.ItemIsUserCheckable)
                    self._table.setItem(row, 0, check)
                check.setData(self.BLOCK_ID_ROLE, block_id)
                check.setCheckState(
                    Qt.Checked if block_id in breakpoints else Qt.Unchecked)
                values = (str(row + 1), block.instance_name,
                          block.block_type, "")
                for column, value in enumerate(values, 1):
                    item = self._table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self._table.setItem(row, column, item)
                    item.setText(value)
                state = ""
                color = QColor("#FFFFFF")
                if block_id == next_id and status["scan_active"]:
                    state = "NEXT"
                    color = QColor("#FFF2CC")
                if block_id == current_id:
                    state = status["last_outcome"].upper()
                    color = QColor("#D7EAF8")
                self._table.item(row, 4).setText(state)
                for column in range(5):
                    self._table.item(row, column).setBackground(color)
                if block_id == target_id:
                    target_row = row
            if target_row >= 0 and self._selected_block_id() != target_id:
                self._table.setCurrentCell(target_row, 2)
        finally:
            self._updating = False

    def _selected_block_id(self) -> str | None:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return item.data(self.BLOCK_ID_ROLE) if item else None

    def _selection_changed(self):
        """Keep controls and the terminal pane on the same row selection."""
        if self._updating:
            return
        self._update_controls()
        self._refresh_terminal_rows()

    @staticmethod
    def _format_terminal_value(value) -> str:
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, float):
            return f"{value:.7g}"
        if value is None:
            return "—"
        return str(value)

    @staticmethod
    def _enum_name(value, fallback: str) -> str:
        name = getattr(value, "name", None)
        if name:
            return str(name).replace("_", " ")
        text = str(value or "").strip()
        return text.replace("_", " ") if text else fallback

    def _refresh_terminal_rows(self):
        """Render the selected compiled block's live value/status triples.

        The compiled graph is already the debugger's execution model.  Reading
        its terminal objects here avoids a second cache that could disagree
        with the values the runtime just executed or forced.
        """
        runtime = self.runtime
        block_id = self._selected_block_id()
        graph = runtime.compiled.graph if runtime and runtime.compiled else None
        block = graph.blocks.get(block_id) if graph and block_id else None
        if block is None:
            self._terminal_title.setText("TERMINALS — Select a block")
            self._terminals.setRowCount(0)
            return

        rows = [
            ("IN", terminal)
            for terminal in block.inputs.values()
        ] + [
            ("OUT", terminal)
            for terminal in block.outputs.values()
        ]
        hidden_count = sum(bool(terminal.hidden) for _direction, terminal in rows)
        hidden_note = f" · {hidden_count} diagnostic" if hidden_count else ""
        if hidden_count != 1 and hidden_count:
            hidden_note += "s"
        self._terminal_title.setText(
            f"TERMINALS — {block.instance_name} ({block.block_type})"
            f" · {len(rows)} total{hidden_note}"
        )
        self._terminals.setRowCount(len(rows))
        for row, (direction, terminal) in enumerate(rows):
            quality = self._enum_name(terminal.status, "UNKNOWN")
            limit = self._enum_name(terminal.limit, "NOT LIMITED")
            values = (
                direction,
                terminal.name,
                self._format_terminal_value(terminal.value),
                quality,
                limit,
                "YES" if terminal.forced else "—",
            )
            for column, value in enumerate(values):
                item = self._terminals.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self._terminals.setItem(row, column, item)
                item.setText(value)
                item.setToolTip(terminal.description)
                # Rows are reused on every 100 ms refresh.  Clear an earlier
                # Bad/limited/forced decoration before applying live state.
                item.setForeground(QBrush())
                item.setBackground(QBrush())

            quality_item = self._terminals.item(row, 3)
            quality_item.setForeground(QColor({
                "GOOD": "#176B3A",
                "UNCERTAIN": "#9A6200",
                "BAD": "#B3261E",
            }.get(quality, "#43515E")))
            if limit != "NOT LIMITED":
                self._terminals.item(row, 4).setForeground(QColor("#9A6200"))
            if terminal.forced:
                forced_item = self._terminals.item(row, 5)
                forced_item.setForeground(QColor("#8A5200"))
                forced_item.setBackground(QColor("#FFF2CC"))
                forced_item.setToolTip(
                    "Forced value: "
                    f"{self._format_terminal_value(terminal.forced_value)}"
                )

    def _update_controls(self):
        runtime = self.runtime
        online = bool(runtime and runtime.is_online)
        paused = bool(online and runtime.is_debug_paused)
        selected = self._selected_block_id() is not None
        self._pause_resume.setEnabled(online)
        self._pause_resume.setText("Resume" if paused else "Pause")
        self._step.setEnabled(paused)
        self._scan.setEnabled(paused)
        self._run_to.setEnabled(online and selected)
        self._abort.setEnabled(
            paused and runtime.get_debug_status()["scan_active"])
        self._clear.setEnabled(bool(runtime and runtime.debug_breakpoints))

    def _toggle_pause(self):
        runtime = self.runtime
        if runtime is None:
            return
        if runtime.is_debug_paused:
            runtime.debug_resume()
        else:
            runtime.debug_pause()
        self.refresh()

    def _step_block(self):
        if self.runtime is not None:
            self.runtime.debug_step_block()
            self.refresh()

    def _run_scan(self):
        if self.runtime is not None:
            self.runtime.debug_run_scan()
            self.refresh()

    def _abort_scan(self):
        if self.runtime is not None:
            self.runtime.debug_abort()
            self.refresh()

    def _run_to_selected(self):
        block_id = self._selected_block_id()
        if self.runtime is not None and block_id:
            self.runtime.debug_run_to_block(block_id)
            self.refresh()

    def _clear_breakpoints(self):
        if self.runtime is not None:
            self.runtime.clear_debug_breakpoints()
            self.refresh()

    def _breakpoint_changed(self, item: QTableWidgetItem):
        if self._updating or item.column() != 0 or self.runtime is None:
            return
        block_id = item.data(self.BLOCK_ID_ROLE)
        if block_id:
            self.runtime.set_debug_breakpoint(
                block_id, item.checkState() == Qt.Checked)
        self.refresh()

    def _activate_block(self, item: QTableWidgetItem):
        check = self._table.item(item.row(), 0)
        block_id = check.data(self.BLOCK_ID_ROLE) if check else None
        canvas = self._canvas_ref()
        if canvas is None:
            return
        scene = getattr(canvas, "scene", None)
        block_item = scene.get_block_item(block_id) if scene and block_id else None
        if block_item is not None:
            scene.clearSelection()
            block_item.setSelected(True)
            view = getattr(canvas, "view", None)
            if view is not None:
                view.centerOn(block_item)
