"""Operator dialogs behind Azeo Operator Station station toolbar actions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


@dataclass(frozen=True)
class SearchRecord:
    kind: str
    name: str
    display: str
    path: str = ""


def station_search_records(deployment, displays=()) -> tuple[SearchRecord, ...]:
    """Published displays and their actual PVM/data-link control tags."""
    reader = getattr(deployment, "monitor_document", deployment.document)
    records = []
    for display in displays or deployment.displays():
        records.append(SearchRecord("Display", display, display))
        document = reader(display) or {}
        paths = set()
        for pvm in document.get("pvms", ()):
            paths.update(str(value) for value in pvm.get("params", {}).values()
                         if str(value).strip())
        for item in document.get("items", ()):
            path = str(item.get("path", "")).strip()
            if path:
                paths.add(path)
            for collection in ("pens", "parameters"):
                paths.update(str(row.get("path", "")).strip()
                             for row in item.get(collection, ())
                             if isinstance(row, dict)
                             and str(row.get("path", "")).strip())
        records.extend(SearchRecord("Control Tag", path, display, path)
                       for path in sorted(paths))
    return tuple(records)


class StationSearchDialog(QDialog):
    """Search published display names and control tags, then navigate."""

    def __init__(self, records, open_display: Callable[[str], bool], parent=None):
        super().__init__(parent)
        self.records = tuple(records)
        self.open_display = open_display
        self.setWindowTitle("Station Search")
        self.resize(720, 450)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Search published displays and control tags"))
        self.query = QLineEdit()
        self.query.setPlaceholderText("Display, module, block, or parameter")
        self.query.textChanged.connect(self.refresh)
        root.addWidget(self.query)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Type", "Name / Tag", "Display"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_selected)
        root.addWidget(self.table, 1)
        bar = QHBoxLayout()
        bar.addStretch()
        open_button = QPushButton("Open")
        open_button.clicked.connect(self.open_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bar.addWidget(open_button)
        bar.addWidget(close)
        root.addLayout(bar)
        self.refresh()

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def filtered_records(self) -> tuple[SearchRecord, ...]:
        needle = self.query.text().strip().casefold()
        return tuple(row for row in self.records if not needle or needle in
                     f"{row.kind} {row.name} {row.display} {row.path}".casefold())

    def refresh(self) -> None:
        rows = self.filtered_records()
        self.table.setRowCount(len(rows))
        for index, record in enumerate(rows):
            for column, text in enumerate(
                    (record.kind, record.name, record.display)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, record)
                self.table.setItem(index, column, item)
        if rows:
            self.table.selectRow(0)

    def open_selected(self) -> bool:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        record = item.data(Qt.UserRole) if item is not None else None
        if record is None:
            return False
        opened = bool(self.open_display(record.display))
        if opened:
            self.accept()
        return opened


class StationErrorsDialog(QDialog):
    """Complete visible-display diagnostics instead of a returned tuple."""

    def __init__(self, errors, parent=None):
        super().__init__(parent)
        self.errors = tuple(errors)
        self.setWindowTitle("Display Errors")
        self.resize(650, 360)
        root = QVBoxLayout(self)
        summary = QLabel(
            f"{len(self.errors)} error(s) on open displays" if self.errors
            else "No errors on open displays")
        root.addWidget(summary)
        self.table = QTableWidget(len(self.errors), 3)
        self.table.setHorizontalHeaderLabels(["Display", "Type", "Detail"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, error in enumerate(self.errors):
            if isinstance(error, tuple):
                display, kind, detail = (*error, "", "")[:3]
            else:
                display, kind, detail = "Open display", "Unregistered PVM", str(error)
            for column, text in enumerate((display, kind, detail)):
                self.table.setItem(row, column, QTableWidgetItem(str(text)))
        root.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)


class AlarmBannerHelpDialog(QDialog):
    """The help reached from the banner's own ``?`` control."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = tuple(rows)
        self.setWindowTitle("Alarm Banner Help")
        self.resize(590, 300)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Alarm-banner controls remain in the same location on every "
            "display. SILENCE is latched only for the alarms currently "
            "visible; a new alarm clears it automatically.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        table = QTableWidget(len(self.rows), 2)
        table.setHorizontalHeaderLabels(["Control", "Action"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, (control, description) in enumerate(self.rows):
            table.setItem(row, 0, QTableWidgetItem(str(control)))
            table.setItem(row, 1, QTableWidgetItem(str(description)))
        root.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)


class DisplayTagSettingsDialog(QDialog):
    """Choose what the display tag above every runtime PVM shows."""

    MODES = (
        ("module", "Module tag", "FIC-101"),
        ("friendly", "Friendly name", "Charge flow controller"),
        ("description", "Description", "Reactor charge flow"),
        ("none", "Hide display tags", "(no tag shown)"),
    )

    def __init__(self, current: str = "module", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display Tag Settings")
        self.resize(470, 220)
        root = QVBoxLayout(self)
        note = QLabel(
            "Select the runtime tag presentation. This is a workstation "
            "view preference; it does not modify or republish the display.")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        self.mode = QComboBox()
        for key, label, example in self.MODES:
            self.mode.addItem(label, key)
            self.mode.setItemData(
                self.mode.count() - 1, f"Example: {example}", Qt.ToolTipRole)
        index = self.mode.findData(current)
        self.mode.setCurrentIndex(max(0, index))
        form.addRow("Show tag as", self.mode)
        self.preview = QLabel()
        form.addRow("Preview", self.preview)
        root.addLayout(form)
        self.mode.currentIndexChanged.connect(self._update_preview)
        self._update_preview()
        buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def _update_preview(self) -> None:
        row = next((row for row in self.MODES
                    if row[0] == self.selected_mode()), self.MODES[0])
        self.preview.setText(row[2])

    def selected_mode(self) -> str:
        return str(self.mode.currentData() or "module")


@dataclass(frozen=True)
class AlarmFilter:
    """Station Alarm List filter. Empty path means all areas."""

    path_prefix: str = ""
    minimum_priority: int = 0
    show_active: bool = True
    show_returned: bool = True
    show_acknowledged: bool = True
    show_unacknowledged: bool = True
    show_suppressed: bool = True

    def matches(self, record) -> bool:
        if self.path_prefix and not record.key.casefold().startswith(
                self.path_prefix.casefold().rstrip("/") + "/") \
                and record.key.casefold() != self.path_prefix.casefold().rstrip("/"):
            return False
        if int(record.priority) < self.minimum_priority:
            return False
        if record.suppressed:
            return self.show_suppressed
        if record.active and not self.show_active:
            return False
        if not record.active and not self.show_returned:
            return False
        return (self.show_acknowledged if record.acknowledged
                else self.show_unacknowledged)


class AlarmFilterDialog(QDialog):
    """Edit the filter used by the station's full Alarm List."""

    def __init__(self, current: AlarmFilter, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alarm Filter")
        self.resize(430, 330)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.path = QLineEdit(current.path_prefix)
        self.path.setPlaceholderText("MODULE or MODULE/BLOCK (blank = all)")
        form.addRow("Source starts with", self.path)
        self.priority = QSpinBox()
        self.priority.setRange(0, 15)
        self.priority.setValue(current.minimum_priority)
        form.addRow("Minimum priority", self.priority)
        root.addLayout(form)
        self.active = QCheckBox("Active alarms")
        self.returned = QCheckBox("Returned, waiting for acknowledgement")
        self.ack = QCheckBox("Acknowledged")
        self.unack = QCheckBox("Unacknowledged")
        self.suppressed = QCheckBox("Suppressed")
        for check, value in (
                (self.active, current.show_active),
                (self.returned, current.show_returned),
                (self.ack, current.show_acknowledged),
                (self.unack, current.show_unacknowledged),
                (self.suppressed, current.show_suppressed)):
            check.setChecked(value)
            root.addWidget(check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def alarm_filter(self) -> AlarmFilter:
        return AlarmFilter(
            self.path.text().strip(), self.priority.value(),
            self.active.isChecked(), self.returned.isChecked(),
            self.ack.isChecked(), self.unack.isChecked(),
            self.suppressed.isChecked())


class AlarmListDialog(QDialog):
    """Live alarm summary with filter, acknowledge, suppress and navigation."""

    def __init__(self, registry, alarm_filter: AlarmFilter,
                 open_source: Callable[[object], bool], parent=None, can_operate=None):
        super().__init__(parent)
        self.registry = registry
        self.alarm_filter = alarm_filter
        self.open_source = open_source
        self.can_operate = can_operate or (lambda: True)
        self._initialized = False
        self.setWindowTitle("Alarm List")
        self.resize(930, 500)
        root = QVBoxLayout(self)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope"))
        self.scope = QLineEdit(alarm_filter.path_prefix)
        self.scope.setPlaceholderText("All areas · enter MODULE or MODULE/BLOCK")
        self.scope.editingFinished.connect(lambda: self.set_filter(
            replace(self.alarm_filter, path_prefix=self.scope.text().strip())))
        scope_row.addWidget(self.scope, 1)
        all_areas = QPushButton("All areas")
        all_areas.clicked.connect(lambda: self.set_filter(replace(self.alarm_filter, path_prefix="")))
        scope_row.addWidget(all_areas)
        root.addLayout(scope_row)
        self.summary = QLabel()
        root.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Prio", "Time", "Source", "Condition", "State", "Ack", "Supp."])
        self.table.horizontalHeaderItem(0).setToolTip("Alarm priority")
        self.table.horizontalHeaderItem(6).setToolTip("Suppressed")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column, width in ((0, 55), (1, 64), (3, 74), (4, 72), (5, 42), (6, 76)):
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_selected)
        root.addWidget(self.table, 1)
        from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
        controls = FlowLayout()
        for text, slot in (("Acknowledge selected", self.acknowledge_selected),
                           ("Suppress / Unsuppress", self.toggle_suppressed),
                           ("Source", self.open_selected),
                           ("Refresh", self.refresh)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            controls.addWidget(button)
        self.close_button = close = QPushButton("Close")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        root.addLayout(controls)
        self.refresh()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def records(self):
        return tuple(row for row in self.registry.records()
                     if self.alarm_filter.matches(row))

    def set_filter(self, alarm_filter):
        self.alarm_filter = alarm_filter
        self.scope.setText(alarm_filter.path_prefix)
        self.refresh()

    def selected_key(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.UserRole) if item is not None else ""

    def refresh(self) -> None:
        selected_key = self.selected_key()
        scroll = self.table.verticalScrollBar().value()
        rows = self.records()
        self.summary.setText(
            f"{self.alarm_filter.path_prefix or 'All areas'} · {len(rows)} shown · "
            f"{len(self.registry.records())} total · minimum priority {self.alarm_filter.minimum_priority}")
        records = {record.key: record for record in rows}
        blocker = QSignalBlocker(self.table)
        # Keep surviving rows in place as new alarms arrive. A timer must not
        # move an acknowledgement target underneath an operator's pointer.
        for index in reversed(range(self.table.rowCount())):
            if self.table.item(index, 0).data(Qt.UserRole) not in records:
                self.table.removeRow(index)
        existing = {self.table.item(index, 0).data(Qt.UserRole): index
                    for index in range(self.table.rowCount())}
        for record in rows:
            index = existing.get(record.key)
            if index is None:
                index = self.table.rowCount()
                self.table.insertRow(index)
            stamp = datetime.fromtimestamp(record.raised_at).strftime("%H:%M:%S")
            state = "ACTIVE" if record.active else "RETURNED"
            values = (record.priority, stamp,
                      f"{record.module}/{record.block}", record.condition,
                      state, "Yes" if record.acknowledged else "No",
                      "Yes" if record.suppressed else "No")
            for column, value in enumerate(values):
                item = self.table.item(index, column)
                if item is None:
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.UserRole, record.key)
                    self.table.setItem(index, column, item)
                elif item.text() != str(value):
                    item.setText(str(value))
        target = next((index for index in range(self.table.rowCount())
                       if self.table.item(index, 0).data(Qt.UserRole) == selected_key), -1)
        if not self._initialized and rows:
            target = 0
        self._initialized = True
        if target >= 0:
            self.table.setCurrentCell(target, 0)
            self.table.selectRow(target)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
        self.table.verticalScrollBar().setValue(scroll)
        del blocker
        if self.selected_key() != selected_key:
            self.table.itemSelectionChanged.emit()

    def selected_record(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        key = item.data(Qt.UserRole) if item is not None else None
        return next((record for record in self.registry.records()
                     if record.key == key), None)

    def acknowledge_selected(self) -> tuple[str, ...]:
        if not self.can_operate():
            self.summary.setText("View only · acknowledgement requires operating authority")
            return ()
        record = self.selected_record()
        changed = self.registry.acknowledge((record.key,)) if record else ()
        self.refresh()
        return changed

    def toggle_suppressed(self) -> bool:
        if not self.can_operate():
            self.summary.setText("View only · suppression requires operating authority")
            return False
        record = self.selected_record()
        changed = bool(record and self.registry.suppress(
            record.key, not record.suppressed))
        self.refresh()
        return changed

    def open_selected(self) -> bool:
        record = self.selected_record()
        return bool(record is not None and self.open_source(record))

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)


class UtilitiesDialog(QDialog):
    """Launcher containing only utilities this station can really open."""

    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.actions = tuple(actions)
        self.setWindowTitle("Utilities")
        self.resize(450, 340)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("OPERATOR UTILITIES"))
        self.list = QListWidget()
        for key, title, description, _callback in self.actions:
            item = QListWidgetItem(f"{title}\n{description}")
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self.launch_selected())
        root.addWidget(self.list)
        bar = QHBoxLayout()
        bar.addStretch()
        launch = QPushButton("Open")
        launch.clicked.connect(self.launch_selected)
        bar.addWidget(launch)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        root.addLayout(bar)
        if self.actions:
            self.list.setCurrentRow(0)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def launch_selected(self) -> bool:
        item = self.list.currentItem()
        key = item.data(Qt.UserRole) if item else None
        row = next((row for row in self.actions if row[0] == key), None)
        if row is None:
            return False
        row[3]()
        return True


class LocalLogonDialog(QDialog):
    """Optional trainer-local identity switch; not a security provider."""

    def __init__(self, user: str, can_operate: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logon / User")
        root = QVBoxLayout(self)
        note = QLabel(
            "This changes the local training-session identity and write role. "
            "It does not authenticate against Azeo or an external directory.")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        self.user = QLineEdit(user)
        form.addRow("User", self.user)
        self.role = QComboBox()
        self.role.addItem("Operator", True)
        self.role.addItem("View Only", False)
        self.role.setCurrentIndex(0 if can_operate else 1)
        form.addRow("Role", self.role)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def identity(self) -> tuple[str, bool]:
        return self.user.text().strip() or "operator", bool(self.role.currentData())


__all__ = [
    "AlarmBannerHelpDialog", "AlarmFilter", "AlarmFilterDialog",
    "AlarmListDialog", "DisplayTagSettingsDialog", "LocalLogonDialog",
    "SearchRecord", "StationErrorsDialog", "StationSearchDialog",
    "UtilitiesDialog", "station_search_records",
]
