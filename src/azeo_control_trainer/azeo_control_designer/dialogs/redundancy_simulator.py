"""Redundant-controller and communication-failure training surface."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    ENGINEERING_QSS, polish_dialog,
)
from azeo_control_trainer.core.strategy.engine.redundancy import (
    ACTIVE, FAILED, NOT_SYNCHRONIZED, STANDBY, RedundancySimulator,
)


_STATE_COLOR = {
    ACTIVE: "#246B46",
    STANDBY: "#2E6BA4",
    FAILED: "#B3261E",
    NOT_SYNCHRONIZED: "#A65A00",
}


class RedundancySimulatorDialog(QDialog):
    """Operate a qualified 1:1 redundancy and field-link failure drill."""

    FAILOVER_DELAY_MS = 750

    def __init__(self, store, executive, model=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.model = model or RedundancySimulator(store, executive)
        self.setWindowTitle("Redundancy and Communication Failure Simulator")
        self.resize(940, 720)
        self.setMinimumSize(780, 600)
        self.setStyleSheet(ENGINEERING_QSS)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._takeover_timer = QTimer(self)
        self._takeover_timer.setSingleShot(True)
        self._takeover_timer.timeout.connect(self._takeover)
        self._build_ui()
        polish_dialog(self)
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        # Closing the drill during its short outage must never leave the real
        # training executive stopped. Promote immediately before destruction.
        if self.model.active_drill is not None:
            self.model.takeover()
        self._takeover_timer.stop()
        self._timer.stop()
        super().closeEvent(event)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        title = QLabel("Redundancy and Communication Failure Simulator")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)
        qualification = QLabel(
            "Training model — not certified redundancy or SIS hardware. "
            "Failover uses the real controller scan interruption and measures "
            "the resulting output bump. Communication faults alter real signal "
            "quality and output-write delivery.")
        qualification.setWordWrap(True)
        qualification.setStyleSheet(
            "background: #FFF4CE; color: #5F4400; border: 1px solid #D8B44A; "
            "padding: 6px;")
        layout.addWidget(qualification)

        pair = QGroupBox("Controller Pair")
        pair_layout = QVBoxLayout(pair)
        cards = QGridLayout()
        (self._primary_card, self._primary_name,
         self._primary_state) = self._member_card("Primary")
        (self._standby_card, self._standby_name,
         self._standby_state) = self._member_card("Standby")
        (self._link_card, self._link_name,
         self._link_state) = self._member_card("Peer Link")
        for column, card in enumerate((self._primary_card,
                                       self._standby_card,
                                       self._link_card)):
            cards.addWidget(card, 0, column)
        pair_layout.addLayout(cards)
        self._sync = QLabel()
        self._sync.setStyleSheet(f"color: {UI.text_secondary};")
        pair_layout.addWidget(self._sync)
        actions = QHBoxLayout()
        for text, callback in (
            ("Fail Primary / Auto Failover", self._fail_primary),
            ("Fail Standby", self._fail_standby),
            ("Recover Standby", self._recover_standby),
            ("Break Peer Link", self._break_link),
            ("Restore Peer Link", self._restore_link),
            ("Reset Pair", self._reset_pair),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        pair_layout.addLayout(actions)
        self._failover_result = QLabel("")
        self._failover_result.setWordWrap(True)
        self._failover_result.setStyleSheet(
            "font-family: Consolas, monospace; color: #37474F;")
        pair_layout.addWidget(self._failover_result)
        layout.addWidget(pair)

        communications = QGroupBox("Field Communication Failure Injection")
        comm_layout = QVBoxLayout(communications)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Tag scope:"))
        self._tag_scope = QLineEdit("*")
        self._tag_scope.setPlaceholderText("* or comma-separated exact store tags")
        scope_row.addWidget(self._tag_scope, 1)
        for text, direction in (
            ("Fail Inputs", "input"),
            ("Fail Outputs", "output"),
            ("Fail Both", "both"),
        ):
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, value=direction: self._inject(value))
            scope_row.addWidget(button)
        clear = QPushButton("Clear All Failures")
        clear.clicked.connect(self._clear_failures)
        scope_row.addWidget(clear)
        comm_layout.addLayout(scope_row)
        self._faults = QTableWidget(0, 6)
        self._faults.setHorizontalHeaderLabels([
            "ID", "Tag", "Direction", "Injected", "Dropped Writes", "Reason"])
        self._faults.setAlternatingRowColors(True)
        self._faults.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._faults.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._faults.verticalHeader().setVisible(False)
        header = self._faults.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        comm_layout.addWidget(self._faults)
        layout.addWidget(communications, 1)

        events = QGroupBox("Redundancy Event Chronicle")
        event_layout = QVBoxLayout(events)
        self._events = QTableWidget(0, 3)
        self._events.setHorizontalHeaderLabels(["Time", "Event", "Detail"])
        self._events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._events.setAlternatingRowColors(True)
        self._events.verticalHeader().setVisible(False)
        self._events.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self._events.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self._events.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        event_layout.addWidget(self._events)
        layout.addWidget(events, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _member_card(title: str):
        frame = QFrame()
        frame.setObjectName("metricCard")
        row = QVBoxLayout(frame)
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet(f"color: {UI.text_secondary};")
        name = QLabel("—")
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        state = QLabel("—")
        state.setAlignment(Qt.AlignCenter)
        state.setStyleSheet("font-weight: bold;")
        row.addWidget(heading)
        row.addWidget(name)
        row.addWidget(state)
        return frame, name, state

    def _fail_primary(self):
        if not self.model.fail_primary():
            self._failover_result.setText(
                "Failover inhibited — inspect synchronization, peer link, "
                "standby state, and online modules.")
            self._refresh()
            return
        self._failover_result.setText(
            f"Primary failed; synchronized standby promotes in "
            f"{self.FAILOVER_DELAY_MS} ms...")
        self._takeover_timer.start(self.FAILOVER_DELAY_MS)
        self._refresh()

    def _takeover(self):
        if self.model.takeover():
            report = self.model.last_report
            if report is not None:
                self._failover_result.setText(report.text())
        self._refresh()

    def _fail_standby(self):
        self.model.fail_standby()
        self._refresh()

    def _recover_standby(self):
        self.model.recover_standby()
        self._refresh()

    def _break_link(self):
        self.model.lose_peer_link()
        self._refresh()

    def _restore_link(self):
        self.model.restore_peer_link()
        self._refresh()

    def _reset_pair(self):
        self._takeover_timer.stop()
        self.model.reset_pair()
        self._refresh()

    def _inject(self, direction: str):
        tags = [part.strip() for part in self._tag_scope.text().split(",")
                if part.strip()]
        if not tags:
            tags = ["*"]
        self._store.inject_communication_failure(
            tags, direction=direction,
            reason=f"Control Designer {direction} link drill")
        self._refresh()

    def _clear_failures(self):
        self._store.clear_communication_failure()
        self._refresh()

    def _refresh(self):
        if (not self.model.synchronized and self.model.peer_link_up
                and self.model.standby_state == STANDBY
                and self.model.executive.online_runtimes()):
            self.model.synchronize()
        status = self.model.status()
        self._primary_name.setText(status.primary)
        self._set_state(self._primary_state, status.primary_state)
        self._standby_name.setText(status.standby)
        self._set_state(self._standby_state, status.standby_state)
        self._link_name.setText("Synchronization")
        self._set_state(self._link_state, status.peer_link)
        last = (datetime.fromtimestamp(status.last_sync).strftime("%H:%M:%S.%f")[:-3]
                if status.last_sync else "never")
        self._sync.setText(
            f"Generation {status.generation} · last sync {last} · "
            f"automatic failover {'READY' if status.failover_ready else 'INHIBITED'}")

        failures = self._store.communication_failures()
        self._faults.setRowCount(len(failures))
        for row, failure in enumerate(failures):
            values = (
                failure["failure_id"], failure["tag"], failure["direction"],
                datetime.fromtimestamp(failure["injected_at"]).strftime("%H:%M:%S"),
                str(failure["dropped_writes"]), failure["reason"])
            for column, value in enumerate(values):
                self._faults.setItem(row, column, QTableWidgetItem(value))

        history = list(reversed(self.model.events[-100:]))
        self._events.setRowCount(len(history))
        for row, event in enumerate(history):
            values = (
                datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[:-3],
                event.event, event.detail)
            for column, value in enumerate(values):
                self._events.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def _set_state(label: QLabel, state: str):
        label.setText(state)
        color = _STATE_COLOR.get(
            state, "#246B46" if state == "HEALTHY" else "#B3261E")
        label.setStyleSheet(f"font-weight: bold; color: {color};")
