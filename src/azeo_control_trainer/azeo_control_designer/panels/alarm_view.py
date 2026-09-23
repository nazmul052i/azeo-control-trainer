"""Active-module alarm engineering view.

The view derives alarms from block schemas rather than maintaining a second
alarm database. Limits remain the block's configuration parameters and live
state remains the block's alarm output (or its PID algorithm state), so
editing or monitoring this pane cannot disagree with the algorithm it
describes.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# Public Azeo spellings first, followed by the internal spellings used by
# blocks whose persisted contract predates the public type catalogue. Looking
# only at schema keys made ALARM's deviation rows disappear and left every
# enable checkbox read-only even though it exposes HH_EN/DEV_EN/etc.
_LIMITS = (
    ("HI_HI_LIM", ("HI_HI_ENAB", "HH_EN"),
     ("HI_HI_ACT", "HI_HI"), "P1"),
    ("HI_LIM", ("HI_ENAB", "HI_EN"),
     ("HI_ACT", "HI"), "P2"),
    ("DV_HI_LIM", ("DV_HI_ENAB", "DEV_EN"),
     ("DV_HI_ACT", "DEV_HI"), "P2"),
    ("DV_LO_LIM", ("DV_LO_ENAB", "DEV_EN"),
     ("DV_LO_ACT", "DEV_LO"), "P2"),
    ("LO_LIM", ("LO_ENAB", "LO_EN"),
     ("LO_ACT", "LO"), "P2"),
    ("LO_LO_LIM", ("LO_LO_ENAB", "LL_EN"),
     ("LO_LO_ACT", "LO_LO"), "P1"),
    ("ROC_LIM", ("ROC_ENAB", "ROC_EN"),
     ("ROC_ACT", "ROC"), "P3"),
)


class AlarmViewPanel(QWidget):
    configEditRequested = Signal(str, str, object)
    blockActivated = Signal(str)

    ROLE_BLOCK_ID = Qt.UserRole
    ROLE_CONFIG_KEY = Qt.UserRole + 1
    ROLE_ENABLE_KEY = Qt.UserRole + 2
    ROLE_STATE_NAMES = Qt.UserRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = None
        self._rebuilding = False
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 3, 4, 3)
        root.setSpacing(2)
        header = QHBoxLayout()
        title = QLabel("ALARM VIEW")
        title.setStyleSheet(f"font-weight: 700; color: {UI.blue};")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("No active module")
        self.summary.setStyleSheet("color: #667789;")
        header.addWidget(self.summary)
        root.addLayout(header)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Block", "Alarm", "Enabled", "Limit", "Priority", "Live state"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 70)
        self.table.cellChanged.connect(self._cell_changed)
        self.table.cellDoubleClicked.connect(self._activate_row)
        root.addWidget(self.table, 1)

    def set_graph(self, graph) -> None:
        self._graph = graph
        self.rebuild()

    @staticmethod
    def _schema_map(block) -> dict[str, str]:
        return {
            str(key).upper(): str(key)
            for key in (block.get_config_schema() or {})
        }

    @staticmethod
    def _config_key(block, schema: dict[str, str], public: str) -> str | None:
        """Resolve a public config name to the block's canonical schema key."""
        direct = schema.get(str(public).upper())
        if direct is not None:
            return direct
        for alias, canonical in (
                getattr(block, "config_aliases", {}) or {}).items():
            if str(alias).upper() == str(public).upper():
                return schema.get(str(canonical).upper())
        return None

    @classmethod
    def _first_config_key(
        cls,
        block,
        schema: dict[str, str],
        public_names: tuple[str, ...],
    ) -> str | None:
        return next(
            (
                key
                for public in public_names
                if (key := cls._config_key(block, schema, public))
            ),
            None,
        )

    @staticmethod
    def _config_value(block, key: str):
        if key in block.config.params:
            return block.config.params[key]
        spec = (block.get_config_schema() or {}).get(key)
        return spec[1] if spec and len(spec) > 1 else ""

    def rebuild(self) -> None:
        self._rebuilding = True
        self.table.setRowCount(0)
        graph = self._graph
        if graph is None:
            self.summary.setText("No active module")
            self._rebuilding = False
            return
        for block in sorted(
            graph.blocks.values(),
            key=lambda candidate: candidate.instance_name.lower(),
        ):
            schema = self._schema_map(block)
            for public_limit, public_enables, public_states, priority in _LIMITS:
                config_key = self._config_key(block, schema, public_limit)
                if config_key is None:
                    continue
                enable_key = self._first_config_key(
                    block, schema, public_enables)
                value = self._config_value(block, config_key)
                enabled = (
                    bool(self._config_value(block, enable_key))
                    if enable_key else self._finite(value)
                )
                row = self.table.rowCount()
                self.table.insertRow(row)

                block_item = QTableWidgetItem(block.instance_name)
                block_item.setData(self.ROLE_BLOCK_ID, block.id)
                block_item.setFlags(block_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 0, block_item)

                alarm_item = QTableWidgetItem(
                    public_limit.removesuffix("_LIM").replace("_", " "))
                alarm_item.setFlags(alarm_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 1, alarm_item)

                enabled_item = QTableWidgetItem()
                enabled_item.setCheckState(
                    Qt.Checked if enabled else Qt.Unchecked)
                enabled_item.setData(self.ROLE_ENABLE_KEY, enable_key or "")
                enabled_item.setFlags(
                    enabled_item.flags() & ~Qt.ItemIsEditable)
                if not enable_key:
                    enabled_item.setFlags(
                        enabled_item.flags() & ~Qt.ItemIsUserCheckable)
                    enabled_item.setToolTip(
                        "This block enables the alarm by configuring a finite limit")
                self.table.setItem(row, 2, enabled_item)

                limit_item = QTableWidgetItem(self._format(value))
                limit_item.setData(self.ROLE_CONFIG_KEY, config_key)
                self.table.setItem(row, 3, limit_item)

                priority_item = QTableWidgetItem(priority)
                priority_item.setFlags(
                    priority_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 4, priority_item)

                state_item = QTableWidgetItem()
                state_item.setData(self.ROLE_STATE_NAMES, public_states)
                state_item.setFlags(state_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 5, state_item)
        self.summary.setText(f"{self.table.rowCount()} configured alarm(s)")
        self._rebuilding = False
        self.refresh_live()

    @staticmethod
    def _finite(value) -> bool:
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return bool(value)

    @staticmethod
    def _format(value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isinf(number):
            return "+∞" if number > 0 else "−∞"
        return f"{number:g}"

    @staticmethod
    def _alarm_state(block, public_names: tuple[str, ...]) -> bool:
        """Read an alarm through terminal aliases or the PID algorithm core."""
        outputs = {
            str(name).upper(): terminal
            for name, terminal in block.outputs.items()
        }
        aliases = {
            str(alias).upper(): str(canonical)
            for alias, canonical in (
                getattr(block, "terminal_aliases", {}) or {}).items()
        }
        for public in public_names:
            terminal = outputs.get(str(public).upper())
            if terminal is not None:
                return bool(terminal.value)
            canonical = aliases.get(str(public).upper())
            terminal = outputs.get(str(canonical).upper()) if canonical else None
            if terminal is not None:
                return bool(terminal.value)

        # PID deliberately does not duplicate six alarm-state output pins; its
        # tested algorithm object is the source of truth for those live states.
        core = getattr(block, "pid_core_block", None)
        alarm_state = getattr(core, "alarm_state", None)
        if alarm_state is not None:
            for public in public_names:
                attr = str(public).lower().removesuffix("_act") + "_act"
                if hasattr(alarm_state, attr):
                    return bool(getattr(alarm_state, attr))
        return False

    def refresh_live(self) -> None:
        if self._graph is None:
            return
        active = 0
        self._rebuilding = True
        for row in range(self.table.rowCount()):
            block_id = self.table.item(row, 0).data(self.ROLE_BLOCK_ID)
            block = self._graph.blocks.get(block_id)
            state_item = self.table.item(row, 5)
            public_names = state_item.data(self.ROLE_STATE_NAMES) or ()
            enabled_item = self.table.item(row, 2)
            enabled = enabled_item.checkState() == Qt.Checked
            state = bool(
                block and enabled
                and self._alarm_state(block, tuple(public_names))
            )
            state_item.setText(
                "ACTIVE" if state else ("Normal" if enabled else "Disabled"))
            state_item.setForeground(
                QColor("#B00020") if state else QColor("#5E6873"))
            active += int(state)
        self._rebuilding = False
        self.summary.setText(
            f"{active} active • {self.table.rowCount()} configured")

    def _cell_changed(self, row: int, column: int) -> None:
        if self._rebuilding or self._graph is None:
            return
        block_item = self.table.item(row, 0)
        if block_item is None:
            return
        block_id = str(block_item.data(self.ROLE_BLOCK_ID))
        if column == 2:
            item = self.table.item(row, column)
            key = item.data(self.ROLE_ENABLE_KEY)
            if key:
                self.configEditRequested.emit(
                    block_id, str(key), item.checkState() == Qt.Checked)
        elif column == 3:
            item = self.table.item(row, column)
            key = item.data(self.ROLE_CONFIG_KEY)
            if key:
                try:
                    value = float(item.text().strip())
                except ValueError:
                    self.rebuild()
                    return
                self.configEditRequested.emit(block_id, str(key), value)

    def _activate_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.blockActivated.emit(str(item.data(self.ROLE_BLOCK_ID)))
