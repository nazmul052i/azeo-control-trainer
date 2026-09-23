"""Function-block faceplate sections — the bodies `faceplate_ui` lacks.

`faceplate_ui` holds the module-faceplate anatomy (title, value, bar,
mode, trend, alarms, unit, buttons). Function-block faceplates need three
more bodies, and they live
here rather than in that module because it is already long and these
are a distinct family — the same split the PVM studio got.

The installed bodies are:

- **`conditions`** — DCC_fp and AT_fp. A tabbed table of conditions.
- **`state_list`** — SEQ_fp and STD_fp. One current state, then rows.
- **`selector`** — Xmtr_fp. Four inputs, an output, an algorithm.

They register themselves into `faceplate_ui.SECTIONS` on import, so a
PVM class names them in `FACEPLATE_LAYOUT` exactly like the built-ins
and `build_sections` keeps enforcing the shared order.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout,
)

from .faceplate_ui import (
    NO_VALUE, Section, _number, register_section, result_of,
)
from ..binding.result import UNRESOLVED
from ..theme.roles import Role
from azeo_control_trainer.core.strategy.model.terminal import Quality


class ConditionTable(Section):
    write_requested = Signal(str, object)

    """Azeo's DCC_fp / AT_fp body: conditions, one per row, in tabs.

    Columns follow the DCC_fp figure — First Out | Condition | Delay |
    Timer | State | Bypass — with the condition's *kind* choosing the
    tab, which is how Azeo splits Interlocks / Permissives / Force
    Setpoints.

    **The Timer cell carries a BAR, and that is why this faceplate is
    worth building at all.** A condition that has been true for 2 of
    its 4 seconds is *about to* trip, and nothing else in the product
    says so: the alarm banner is silent until it has tripped, and by
    then the operator has lost the two seconds in which they could have
    acted. `timer / delay` is drawn as a ground behind the number.

    **The bar deepens by ALPHA, not by hue.** A cell that turned red
    near its limit would be a second alarm vocabulary arguing with the
    banner above it; this is a warning *of* an alarm, not one.

    **An inactive condition is greyed, never hidden.** The manual says
    the description "is shown as light gray" when inactive. Dropping
    the row instead would make the table change length as the plant
    moves, and a row that shifts under the eye gets misread under
    exactly the stress this screen exists for.

    Bound to `{path}/CONDITIONS`, which `LiveGraphSource` synthesises
    for any block exposing `condition_table()`.
    """

    #: The manual's tabs, against our `condition_table()` kinds. A kind
    #: with no rows keeps its tab, so the strip does not move between
    #: modules — the same argument as greying rather than hiding.
    TABS = (("interlock", "Interlocks"), ("permissive", "Permissives"),
            ("trip", "Trips"))
    COLUMNS = ("", "Condition", "Delay", "Timer", "State", "Byp")
    WIDTHS = (28, 260, 64, 70, 100, 48)

    def __init__(self, palette, parent=None, *, show_commands: bool = True):
        super().__init__(palette, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.reset_button = None
        if show_commands:
            commands = QHBoxLayout()
            self.reset_button = QPushButton("Reset")
            self.reset_button.setFixedSize(58, 24)
            self.reset_button.clicked.connect(
                lambda: self.write_requested.emit("cmd.reset", True))
            commands.addWidget(self.reset_button)
            commands.addStretch(1)
            lay.addLayout(commands)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { background: %s; color: %s; padding: 2px 8px;"
            " font-size: 9px; }"
            "QTabBar::tab:selected { background: %s; color: %s; }"
            "QTabWidget::pane { border: 1px solid %s; }" % (
                self.colour(Role.SURFACE_PANEL_ALT),
                self.colour(Role.TEXT_DIM),
                self.colour(Role.SURFACE_FIELD),
                self.colour(Role.TEXT), self.colour(Role.LINE)))
        self._tables = {}
        for kind, title in self.TABS:
            table = QTableWidget(0, len(self.COLUMNS))
            table.setHorizontalHeaderLabels(list(self.COLUMNS))
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            for column, width in enumerate(self.WIDTHS):
                table.setColumnWidth(column, width)
            table.setFixedHeight(350)
            table.setStyleSheet(
                "background: %s; gridline-color: %s; font-size: 9px;" % (
                    self.colour(Role.SURFACE_PANEL),
                    self.colour(Role.LINE)))
            self._tables[kind] = table
            self.tabs.addTab(table, title)
        lay.addWidget(self.tabs)
        self.first_out = QLabel("")
        self.first_out.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.first_out.setStyleSheet(
            "color: %s;" % self.colour(Role.ALARM_P1_TEXT))
        lay.addWidget(self.first_out)

    def table_for(self, kind: str):
        """The QTableWidget for one kind of condition.

        Public because the shape is part of the contract: Azeo puts
        interlocks, permissives and trips on SEPARATE tabs, and a
        caller that wants "the rows" has to say which kind it means.
        The old single-table panel let callers skip that question, and
        the answer they got silently mixed the three.
        """
        return self._tables[kind]

    @staticmethod
    def rows_of(bound) -> list:
        """The condition table, decoded.

        Bad or absent yields NO rows. An empty table is honest; a table
        of invented rows tells the operator the interlocks are healthy.
        """
        result = result_of(bound, "conditions")
        if result is UNRESOLVED or result.quality is Quality.BAD:
            return []
        try:
            rows = json.loads(result.value or "[]")
        except (TypeError, ValueError):
            return []
        return rows if isinstance(rows, list) else []

    def refresh(self, bound) -> None:
        rows = self.rows_of(bound)
        latched = ""
        for kind, _title in self.TABS:
            table = self._tables[kind]
            mine = [r for r in rows if r.get("kind") == kind]
            table.setRowCount(len(mine))
            for i, row in enumerate(mine):
                if row.get("first_out"):
                    latched = str(row.get("description", ""))
                self._fill(table, i, row)
        self.first_out.setText("First out: %s" % latched if latched else "")
        self.first_out.setVisible(bool(latched))

    def progress(self, row) -> float | None:
        """How far a condition has run toward its delay, 0..1.

        None when the condition has no delay — there is nothing to be
        part-way through, and a bar at 0 % would imply otherwise.
        """
        delay = float(row.get("delay", 0.0) or 0.0)
        if delay <= 0:
            return None
        timer = float(row.get("timer", 0.0) or 0.0)
        return max(0.0, min(1.0, timer / delay))

    @staticmethod
    def healthy(row) -> bool:
        """Whether a condition is in its GOOD state.

        **A trip inverts.** A permissive or interlock reads True when
        it is satisfied; a trip reads True when it has TRIPPED. Taking
        `state` at face value for every kind reports a tripped
        condition as "OK", which is the one thing this table exists to
        never do.
        """
        state = bool(row.get("state"))
        return (not state) if row.get("kind") == "trip" else state

    @staticmethod
    def state_text(row) -> str:
        """What the State cell says, in the kind's own vocabulary."""
        if row.get("kind") == "trip":
            return "ACTIVE" if bool(row.get("state")) else "OK"
        return "OK" if bool(row.get("state")) else "NOT MET"

    def _fill(self, table, i, row) -> None:
        good = self.healthy(row)
        delay = float(row.get("delay", 0.0) or 0.0)
        timer = float(row.get("timer", 0.0) or 0.0)
        cells = ("➤" if row.get("first_out") else "",
                 str(row.get("description", "")),
                 "%.1f" % delay if delay else "",
                 "%.1f" % timer if delay or timer else "",
                 self.state_text(row),
                 "✓" if row.get("bypassed") else "")
        fraction = self.progress(row)
        for column, text in enumerate(cells):
            cell = QTableWidgetItem(text)
            # Greyed when NOT the condition demanding attention. The
            # manual greys an inactive condition; for a trip the
            # attention-worthy case is the active one, so this follows
            # health rather than raw state.
            role = Role.TEXT if good else Role.TEXT_DIM
            if column == 0 and row.get("first_out"):
                role = Role.ALARM_P1_TEXT
            if column == 4 and not good:
                role = Role.ALARM_P1_TEXT
            cell.setForeground(QColor(self.colour(role)))
            if column == 3 and fraction is not None:
                cell.setBackground(self.bar_colour(fraction))
                cell.setToolTip("%.1f s of %.1f s (%.0f%% toward trip)"
                                % (timer, delay, fraction * 100))
            table.setItem(i, column, cell)

    def bar_colour(self, fraction: float) -> QColor:
        """The timer cell's ground, deepening toward the delay."""
        colour = QColor(self.colour(Role.ALARM_P2))
        colour.setAlpha(int(30 + 150 * max(0.0, min(1.0, fraction))))
        return colour


class StateList(Section):
    """Azeo's SEQ_fp / STD_fp body: a current state, then its rows.

    SEQ_fp lists the block's outputs (`DESC_OUTx`, coloured by
    `OUT_Dx`); STD_fp lists each transition condition beside the state
    it would move to. Both are *one current state, then a labelled
    list*, which is why one section serves both rather than two
    near-identical ones.
    """

    ROWS = 16

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        caption = QLabel("Current State")
        caption.setFont(QFont("Segoe UI", 8))
        caption.setAlignment(Qt.AlignCenter)
        caption.setStyleSheet("color: %s;" % self.colour(Role.TEXT_DIM))
        lay.addWidget(caption)
        self.state = QLabel(NO_VALUE)
        self.state.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.state.setAlignment(Qt.AlignCenter)
        self.state.setStyleSheet("color: %s;" % self.colour(Role.ACTION))
        lay.addWidget(self.state)
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 90)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setShowGrid(False)
        self.table.setFixedHeight(330)
        self.table.setStyleSheet(
            "background: %s; gridline-color: %s; font-size: 9px;" % (
                self.colour(Role.SURFACE_PANEL), self.colour(Role.LINE)))
        lay.addWidget(self.table)

    def refresh(self, bound) -> None:
        state = result_of(bound, "state.name")
        unreadable = (state is UNRESOLVED
                      or state.quality is Quality.BAD
                      or state.value is None)
        self.state.setText(NO_VALUE if unreadable else str(state.value))
        rows = []
        for i in range(1, self.ROWS + 1):
            label = result_of(bound, "row%d" % i)
            if label is UNRESOLVED or label.value in (None, ""):
                continue
            flag = result_of(bound, "row%d.state" % i)
            on = (flag is not UNRESOLVED
                  and flag.quality is not Quality.BAD
                  and bool(flag.value))
            rows.append((str(label.value), on))
        self.table.setRowCount(len(rows))
        for i, (text, on) in enumerate(rows):
            for column, value in enumerate((text, "ON" if on else "")):
                cell = QTableWidgetItem(value)
                cell.setForeground(QColor(self.colour(
                    Role.TEXT if on else Role.TEXT_DIM)))
                self.table.setItem(i, column, cell)


class InputSelector(Section):
    """Azeo's Xmtr_fp body: four inputs, the output, the algorithm.

    The manual carries input status in the value's TEXT COLOUR (Good /
    Uncertain / Bad), which is what our display-state contract already
    says — so this reads the binding's quality rather than growing a
    second status vocabulary beside it.
    """

    INPUTS = 4

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._rows = []
        for i in range(1, self.INPUTS + 1):
            row = QHBoxLayout()
            row.setSpacing(4)
            disabled = QLabel("☐")
            disabled.setFixedWidth(14)
            disabled.setFont(QFont("Segoe UI", 8))
            disabled.setStyleSheet("color: %s;" % self.colour(Role.TEXT_DIM))
            name = QLabel("Input %d" % i)
            name.setFont(QFont("Segoe UI", 8))
            name.setStyleSheet("color: %s;" % self.colour(Role.TEXT_DIM))
            value = QLabel(NO_VALUE)
            value.setFont(QFont("Consolas", 9))
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(disabled)
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(value)
            lay.addLayout(row)
            self._rows.append((disabled, name, value))
        out = QHBoxLayout()
        label = QLabel("Output")
        label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        label.setStyleSheet("color: %s;" % self.colour(Role.TEXT))
        self.output = QLabel(NO_VALUE)
        self.output.setFont(QFont("Consolas", 10, QFont.Bold))
        self.output.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        out.addWidget(label)
        out.addStretch(1)
        out.addWidget(self.output)
        lay.addLayout(out)
        selection_caption = QLabel("Selection Type")
        selection_caption.setFont(QFont("Segoe UI", 8, QFont.Bold))
        selection_caption.setStyleSheet(
            "color: %s;" % self.colour(Role.TEXT))
        lay.addWidget(selection_caption)
        self.algorithm = QLabel("")
        self.algorithm.setFont(QFont("Segoe UI", 8))
        self.algorithm.setStyleSheet(
            "color: %s;" % self.colour(Role.TEXT_DIM))
        lay.addWidget(self.algorithm)
        operator_caption = QLabel("Operator Selection")
        operator_caption.setFont(QFont("Segoe UI", 8, QFont.Bold))
        operator_caption.setStyleSheet(
            "color: %s;" % self.colour(Role.TEXT))
        lay.addWidget(operator_caption)
        self._operator = []
        for i in range(1, self.INPUTS + 1):
            option = QLabel("○  Use Input %d" % i)
            option.setFont(QFont("Segoe UI", 8))
            option.setStyleSheet(
                "color: %s;" % self.colour(Role.TEXT_DIM))
            lay.addWidget(option)
            self._operator.append(option)
        self.setFixedHeight(330)

    def tone(self, result) -> Role:
        """Which role a value's text takes, from its quality alone."""
        if result is UNRESOLVED or result.quality is Quality.BAD:
            return Role.ALARM_P1_TEXT
        if getattr(result.quality, "name", "") == "UNCERTAIN":
            return Role.ALARM_P2_TEXT
        return Role.TEXT

    def refresh(self, bound) -> None:
        selected_result = result_of(bound, "selected")
        selected = None if selected_result is UNRESOLVED \
            else selected_result.value
        for i, (disabled, name, value) in enumerate(
                self._rows, start=1):
            entry = result_of(bound, "in%d.value" % i)
            off = result_of(bound, "in%d.disabled" % i)
            present = entry is not UNRESOLVED
            for widget in (disabled, name, value, self._operator[i - 1]):
                widget.setVisible(present)
            disabled.setText("☒" if (off is not UNRESOLVED and off.value)
                             else "☐")
            value.setText(_number(entry))
            value.setStyleSheet("color: %s;" % self.colour(self.tone(entry)))
            picked = str(selected).upper() in (
                str(i), "IN%d" % i, "IN_%d" % i, "INPUT %d" % i)
            self._operator[i - 1].setText(
                "%s  Use Input %d" % ("●" if picked else "○", i))
        out = result_of(bound, "out.value")
        self.output.setText(_number(out))
        self.output.setStyleSheet("color: %s;" % self.colour(self.tone(out)))
        algorithm = result_of(bound, "select_type")
        self.algorithm.setText(
            "" if algorithm is UNRESOLVED or algorithm.value is None
            else "Selection: %s" % algorithm.value)


class VoterPanel(Section):
    """AVTR/DVTR's five operational tabs over as many as 16 inputs.

    The compact voter PVM answers whether the block has enough votes;
    this faceplate answers *which* transmitters voted, whether one was
    bypassed, what is keeping startup inhibited, and which alert explains
    the state. Those are five separate operator questions in the manual,
    so they stay five tabs rather than one table with shifting columns.
    """

    MAX_INPUTS = 16
    ALERTS = (
        ("alarm.trip", "Trip Active"),
        ("alarm.pre_trip", "Pre-Trip Active"),
        ("alarm.bypass", "Bypass Active"),
        ("alarm.startup", "Startup Override"),
        ("alarm.deviation", "Deviation Limit"),
        ("alarm.expiration", "Expiration Reminder"),
        ("alarm.input_bad", "Input Bad"),
    )

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { background: %s; color: %s; padding: 3px 7px;"
            " font-size: 8px; }"
            "QTabBar::tab:selected { background: %s; color: %s; }"
            "QTabWidget::pane { border: 1px solid %s; }" % (
                self.colour(Role.SURFACE_PANEL_ALT),
                self.colour(Role.TEXT_DIM),
                self.colour(Role.SURFACE_FIELD),
                self.colour(Role.TEXT), self.colour(Role.LINE)))
        layout.addWidget(self.tabs)

        self.trip_summary, self.trip = self._input_tab(
            "Trip", ("Input", "Value", "Vote"))
        self.pre_summary, self.pre_trip = self._input_tab(
            "Pre-Trip", ("Input", "Value", "Vote"))
        self.bypass_summary, self.bypass = self._input_tab(
            "Bypass", ("Input", "Description", "Bypassed"))
        self.startup = self._label_tab("Startup")
        self.alerts = QTableWidget(0, 2)
        self._prepare_table(self.alerts, ("Alert", "State"), (180, 70))
        self.tabs.addTab(self.alerts, "Alert")

    def _input_tab(self, title, columns):
        page = QVBoxLayout()
        host = QLabel()
        host.setLayout(page)
        summary = QLabel("")
        summary.setFont(QFont("Segoe UI", 8, QFont.Bold))
        summary.setStyleSheet("color: %s;" % self.colour(Role.TEXT))
        page.addWidget(summary)
        table = QTableWidget(0, len(columns))
        self._prepare_table(table, columns, (48, 132, 62))
        page.addWidget(table)
        self.tabs.addTab(host, title)
        return summary, table

    def _label_tab(self, title):
        label = QLabel("")
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        label.setWordWrap(True)
        label.setMargin(8)
        label.setStyleSheet("color: %s;" % self.colour(Role.TEXT))
        self.tabs.addTab(label, title)
        return label

    def _prepare_table(self, table, columns, widths):
        table.setHorizontalHeaderLabels(list(columns))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setFixedHeight(176)
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
        table.setStyleSheet(
            "background: %s; gridline-color: %s; font-size: 9px;" % (
                self.colour(Role.SURFACE_FIELD), self.colour(Role.LINE)))

    @staticmethod
    def _value(bound, key, default=None):
        result = result_of(bound, key)
        return default if result is UNRESOLVED or result.value is None \
            else result.value

    def _fill_input_table(self, table, rows, abnormal_column=2):
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            abnormal = bool(row[abnormal_column])
            for column, value in enumerate(row):
                if isinstance(value, bool):
                    text = "YES" if value else ""
                elif isinstance(value, float):
                    text = "%g" % value
                else:
                    text = str(value)
                cell = QTableWidgetItem(text)
                cell.setForeground(QColor(self.colour(
                    Role.ALARM_P1_TEXT if abnormal and column == abnormal_column
                    else Role.TEXT if abnormal else Role.TEXT_DIM)))
                table.setItem(row_index, column, cell)

    def refresh(self, bound) -> None:
        try:
            count = max(1, min(self.MAX_INPUTS, int(
                self._value(bound, "num_inputs", 3))))
        except (TypeError, ValueError):
            count = 3
        needed = self._value(bound, "votes_required", "–")
        votes = self._value(bound, "trip_votes", "–")
        self.trip_summary.setText(
            "Trip status: %s    Votes: %s / %s" % (
                self._value(bound, "trip_status", "Unknown"), votes, needed))
        self.pre_summary.setText(
            "Pre-trip status: %s    Votes: %s / %s" % (
                self._value(bound, "pre_status", "Not available"),
                self._value(bound, "pre_votes", "–"), needed))

        trip_rows = []
        pre_rows = []
        bypass_rows = []
        has_pre_trip = False
        for index in range(1, count + 1):
            description = self._value(
                bound, "in%d.description" % index, "Input %d" % index)
            value = self._value(bound, "in%d.value" % index, "–")
            vote = bool(self._value(bound, "in%d.vote" % index, False))
            pre = result_of(bound, "in%d.pre_vote" % index)
            pre_vote = pre is not UNRESOLVED and bool(pre.value)
            has_pre_trip |= pre is not UNRESOLVED
            bypassed = bool(self._value(
                bound, "in%d.bypassed" % index, False))
            trip_rows.append((index, value, vote))
            pre_rows.append((index, value, pre_vote))
            bypass_rows.append((index, description, bypassed))
        self._fill_input_table(self.trip, trip_rows)
        self._fill_input_table(self.pre_trip, pre_rows)
        self._fill_input_table(self.bypass, bypass_rows)
        self.tabs.setTabEnabled(1, has_pre_trip)
        self.bypass_summary.setText(
            "Permit: %s    Remaining: %s s" % (
                "GRANTED" if self._value(bound, "bypass_permit", False)
                else "NOT GRANTED",
                self._value(bound, "bypass_timer", 0)))
        self.startup.setText(
            "State: %s\nRemaining: %s s\nStable: %s s\n"
            "Time to stable: %s s" % (
                "INHIBITED" if self._value(bound, "startup.active", False)
                else "Normal",
                self._value(bound, "startup.timer", 0),
                self._value(bound, "startup.stable", 0),
                self._value(bound, "startup.time_to_stable", 0)))

        alert_rows = []
        for key, label in self.ALERTS:
            active = bool(self._value(bound, key, False))
            alert_rows.append((label, "ACTIVE" if active else ""))
        self.alerts.setRowCount(len(alert_rows))
        for row_index, (label, state) in enumerate(alert_rows):
            for column, text in enumerate((label, state)):
                cell = QTableWidgetItem(text)
                cell.setForeground(QColor(self.colour(
                    Role.ALARM_P1_TEXT if state else Role.TEXT_DIM)))
                self.alerts.setItem(row_index, column, cell)


# The bodies slot in after `mode` and before `trend`: identity, then
# values, then the body, then history — the order the module
# faceplates already read in.
register_section("conditions", ConditionTable, after="mode")
register_section("state_list", StateList, after="conditions")
register_section("selector", InputSelector, after="state_list")
register_section("voter", VoterPanel, after="selector")
