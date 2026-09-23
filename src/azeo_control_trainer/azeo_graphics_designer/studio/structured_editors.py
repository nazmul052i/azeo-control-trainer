"""Structured editors for compound data, writable entries and actions.

Raw JSON remains available for uncommon extension fields, but it is an
explicit advanced operation.  The ordinary workflow presents rows and typed
fields and returns a validated copy; callers own the single undo transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.hmi.pvms.elements import (
    ACTION_KINDS,
    ALARM_LIST,
    CHART,
    DATE_TIME,
    MOUSE_EVENTS,
    MULTI_POINT,
    RADAR_PLOT,
    TABLE,
    TAB,
    USER_ENTRY_TITLES,
    Action,
    UserEntry,
)
from .binding_editor import (
    BindingCatalog,
    BindingKind,
    BindingTarget,
    UnifiedBindingEditor,
    ValueType,
)
from .editor_models import (
    action_issues,
    data_issues,
    data_payload,
    merge_actions,
    merge_data_payload,
    user_entry_issues,
)


def _scalar(text: str):
    value = text.strip()
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return float(value) if any(ch in value for ch in ".eE") else int(value)
    except ValueError:
        return value


def _messages(issues) -> str:
    return "\n".join(f"{issue.field}: {issue.message}" for issue in issues)


def choose_path(
    parent, catalog: BindingCatalog, initial: str = "", *, writable: bool = False
) -> str | None:
    """Choose a string-path binding; reject descriptor-only source kinds."""
    target = BindingTarget("Path", ValueType.ANY, "Selection", writable)
    dialog = UnifiedBindingEditor(target, catalog, parent=parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    result = dialog.binding_result()
    if result is None or result.kind not in {
        BindingKind.DIRECT_TAG,
        BindingKind.INDIRECT_TAG,
        BindingKind.CLASS_PROPERTY,
    }:
        QMessageBox.warning(
            parent,
            "Path binding",
            "This field stores a tag path. Choose a direct, indirect, or class-property source.",
        )
        return None
    return result.source


class _AdvancedJsonDialog(QDialog):
    def __init__(self, value, validator, noun: str, parent=None):
        super().__init__(parent)
        self._validator = validator
        self._value = None
        self.setWindowTitle(f"Advanced {noun} JSON")
        self.resize(650, 480)
        lay = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            lay,
            f"Advanced {noun} JSON",
            "Edit extension fields directly. The same structured validation "
            "must pass before changes can be applied.",
        )
        note = QLabel(
            "Advanced extension editor. JSON is validated against the same "
            "rules as the structured form before it can be applied."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        self.text = QPlainTextEdit(json.dumps(value, indent=2, ensure_ascii=False))
        self.text.setFontFamily("Consolas")
        lay.addWidget(self.text, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)

    @property
    def value(self):
        return deepcopy(self._value)

    def _accept(self):
        try:
            value = json.loads(self.text.toPlainText())
            issues = self._validator(value)
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                raise ValueError(_messages(errors))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.status.setText(str(error))
            return
        self._value = value
        self.accept()


class DataElementDialog(QDialog):
    """Row-oriented editor for the existing compound-element grammar."""

    def __init__(self, data: dict, catalog: BindingCatalog, parent=None):
        super().__init__(parent)
        self.original = deepcopy(data)
        self.catalog = catalog
        self.kind = str(data.get("kind", ""))
        self._advanced = None
        self._result = None
        self.setWindowTitle(f"Configure {self.kind.replace('_', ' ').title()}")
        self.resize(720, 500)
        lay = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            lay,
            f"Configure {self.kind.replace('_', ' ').title()}",
            "Define presentation fields, rows and governed data bindings.",
        )
        self.form = QFormLayout()
        lay.addLayout(self.form)
        self.rows = QTableWidget()
        self.rows.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.rows, 1)
        self._layout = lay
        row_buttons = QHBoxLayout()
        self.add_row = QPushButton("Add")
        self.remove_row = QPushButton("Remove")
        self.bind_row = QPushButton("Bind Path…")
        self.advanced = QPushButton("Advanced JSON…")
        for button in (self.add_row, self.remove_row, self.bind_row):
            row_buttons.addWidget(button)
        row_buttons.addStretch(1)
        row_buttons.addWidget(self.advanced)
        lay.addLayout(row_buttons)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)
        self.add_row.clicked.connect(self._add)
        self.remove_row.clicked.connect(self._remove)
        self.bind_row.clicked.connect(self._bind)
        self.advanced.clicked.connect(self._edit_advanced)
        self._build(data_payload(data))

    def _spin(self, low, high, value, decimals=0):
        if decimals:
            field = QDoubleSpinBox()
            field.setDecimals(decimals)
        else:
            field = QSpinBox()
        field.setRange(low, high)
        field.setValue(value)
        return field

    def _build(self, payload):
        self.fields = {}
        if self.kind == CHART:
            field = QLineEdit(str(payload.get("series_path", "")))
            field.setPlaceholderText("Optional @procedure/{ProcedureRef}/TREND · leave pens empty")
            self.fields["series_path"] = field
            self.form.addRow("Shared procedure history", field)
            self.rows.setColumnCount(3)
            self.rows.setHorizontalHeaderLabels(["Label", "Path", "Color"])
            self._load_rows(payload.get("pens", ()), ("label", "path", "color"))
            for key, label, value in (
                ("lo", "Scale minimum", payload.get("lo", 0)),
                ("hi", "Scale maximum", payload.get("hi", 100)),
                ("window_seconds", "Window seconds", payload.get("window_seconds", 60)),
            ):
                field = self._spin(-1_000_000, 1_000_000, value, 2)
                self.fields[key] = field
                self.form.addRow(label, field)
        elif self.kind in (MULTI_POINT, RADAR_PLOT):
            self.rows.setColumnCount(4)
            self.rows.setHorizontalHeaderLabels(["Label", "Path", "Minimum", "Maximum"])
            self._load_rows(payload.get("parameters", ()), ("label", "path", "lo", "hi"))
        elif self.kind == TAB:
            self.rows.setColumnCount(2)
            self.rows.setHorizontalHeaderLabels(["Title", "Text"])
            self._load_rows(payload.get("tabs", ()), ("title", "text"))
            active = self._spin(0, 31, payload.get("active_tab", 0))
            self.fields["active_tab"] = active
            self.form.addRow("Active tab", active)
            self.bind_row.hide()
        elif self.kind == TABLE:
            for key, label, choices in (("presentation", "Presentation", ["table", "workflow"]),
                                         ("row_action", "Row action", ["", "tune"])):
                field = QComboBox()
                field.addItems(choices)
                field.setCurrentText(str(payload.get(key, choices[0])))
                self.fields[key] = field
                self.form.addRow(label, field)
            for key, label in (("command_context", "Procedure command context"), ("empty_text", "Empty collection message")):
                field = QLineEdit(str(payload.get(key, "")))
                self.fields[key] = field
                self.form.addRow(label, field)
            source = QLineEdit(str(payload.get("rows_path", "")))
            source.setPlaceholderText("Optional live row collection, e.g. @procedure/{ProcedureRef}/STEPS")
            self.fields["rows_path"] = source
            self.form.addRow("Live rows source", source)
            row_help = QComboBox()
            row_help.addItems(["false", "true"])
            row_help.setCurrentText("true" if payload.get("row_help") else "false")
            self.fields["row_help"] = row_help
            self.form.addRow("Procedure row Block Help", row_help)
            self.rows.setColumnCount(3)
            self.rows.setHorizontalHeaderLabels(["Column key", "Title", "Width"])
            self._load_rows(payload.get("columns", ()), ("key", "title", "width"))
            columns = [
                str(column.get("key", ""))
                for column in payload.get("columns", ())
                if isinstance(column, dict)
            ]
            self.table_cells = QTableWidget(0, len(columns))
            self.table_cells.setHorizontalHeaderLabels(columns)
            self.table_cells.horizontalHeader().setStretchLastSection(True)
            self._layout.insertWidget(2, QLabel("Table rows"))
            self._layout.insertWidget(3, self.table_cells, 1)
            for source_row in payload.get("rows", ()):
                if not isinstance(source_row, dict):
                    continue
                target_row = self.table_cells.rowCount()
                self.table_cells.insertRow(target_row)
                for column, key in enumerate(columns):
                    value = source_row.get(key, "")
                    if isinstance(value, dict):
                        cell = QTableWidgetItem(str(value.get("path", "")))
                        cell.setData(Qt.UserRole, deepcopy(value))
                    else:
                        cell = QTableWidgetItem(str(value))
                    self.table_cells.setItem(target_row, column, cell)
            table_buttons = QHBoxLayout()
            add_table_row = QPushButton("Add table row")
            remove_table_row = QPushButton("Remove table row")
            bind_table_cell = QPushButton("Bind selected cell…")
            add_table_row.clicked.connect(
                lambda: self.table_cells.insertRow(self.table_cells.rowCount())
            )
            remove_table_row.clicked.connect(self._remove_table_rows)
            bind_table_cell.clicked.connect(self._bind_table_cell)
            for button in (add_table_row, remove_table_row, bind_table_cell):
                table_buttons.addWidget(button)
            table_buttons.addStretch(1)
            self._layout.insertLayout(4, table_buttons)
            for key, label, value in (
                ("row_height", "Row height", payload.get("row_height", 20)),
                ("header_height", "Header height", payload.get("header_height", 22)),
            ):
                field = self._spin(1, 500, value)
                self.fields[key] = field
                self.form.addRow(label, field)
            self.bind_row.hide()
        elif self.kind == ALARM_LIST:
            self.rows.hide()
            self.add_row.hide()
            self.remove_row.hide()
            self.bind_row.hide()
            for key, label, lo, hi, value in (
                ("priority_min", "Minimum priority", 0, 255, payload.get("priority_min", 0)),
                ("max_rows", "Maximum rows", 1, 1000, payload.get("max_rows", 25)),
            ):
                field = self._spin(lo, hi, value)
                self.fields[key] = field
                self.form.addRow(label, field)
            prefix = QLineEdit(str(payload.get("path_prefix", "")))
            self.fields["path_prefix"] = prefix
            self.form.addRow("Path prefix", prefix)
        elif self.kind == DATE_TIME:
            self.rows.hide()
            self.add_row.hide()
            self.remove_row.hide()
            self.bind_row.hide()
            zone = AuthoringComboBox()
            zone.addItems(["local", "utc"])
            zone.setCurrentText(str(payload.get("timezone", "local")))
            self.fields["timezone"] = zone
            self.form.addRow("Timezone", zone)
            for key, label in (("format", "Format"), ("culture", "Culture")):
                field = QLineEdit(str(payload.get(key, "")))
                self.fields[key] = field
                self.form.addRow(label, field)
        elif self.kind == "symbol":
            self.rows.setColumnCount(3)
            self.rows.setHorizontalHeaderLabels(["Port name", "X (0..1)", "Y (0..1)"])
            self._load_rows(payload.get("ports", ()), ("name", "x", "y"))
            self.bind_row.hide()

    def _load_rows(self, rows, keys):
        for value in rows:
            row = self.rows.rowCount()
            self.rows.insertRow(row)
            for col, key in enumerate(keys):
                self.rows.setItem(
                    row,
                    col,
                    QTableWidgetItem(str(value.get(key, "")) if isinstance(value, dict) else ""),
                )

    def _add(self):
        self.rows.insertRow(self.rows.rowCount())

    def _remove(self):
        for index in sorted({i.row() for i in self.rows.selectedIndexes()}, reverse=True):
            self.rows.removeRow(index)

    def _remove_table_rows(self):
        for row in sorted(
            {index.row() for index in self.table_cells.selectedIndexes()},
            reverse=True,
        ):
            self.table_cells.removeRow(row)

    def _bind_table_cell(self):
        row, column = self.table_cells.currentRow(), self.table_cells.currentColumn()
        if row < 0 or column < 0:
            return
        chosen = choose_path(self, self.catalog)
        if chosen is None:
            return
        cell = QTableWidgetItem(chosen)
        cell.setData(Qt.UserRole, {"path": chosen, "type": "numeric"})
        self.table_cells.setItem(row, column, cell)

    def _bind(self):
        row = self.rows.currentRow()
        if row < 0 or self.rows.columnCount() < 2:
            return
        chosen = choose_path(self, self.catalog)
        if chosen is not None:
            self.rows.setItem(row, 1, QTableWidgetItem(chosen))

    def _payload(self):
        if self._advanced is not None:
            return deepcopy(self._advanced)
        payload = {}
        for key, field in self.fields.items():
            if isinstance(field, QComboBox):
                value = field.currentText()
            elif isinstance(field, (QSpinBox, QDoubleSpinBox)):
                value = field.value()
            else:
                value = field.text().strip()
            if value != "":
                payload[key] = value == "true" if key == "row_help" else value
        definitions = {
            CHART: ("pens", ("label", "path", "color")),
            MULTI_POINT: ("parameters", ("label", "path", "lo", "hi")),
            RADAR_PLOT: ("parameters", ("label", "path", "lo", "hi")),
            TAB: ("tabs", ("title", "text")),
            TABLE: ("columns", ("key", "title", "width")),
            "symbol": ("ports", ("name", "x", "y")),
        }
        if self.kind in definitions:
            name, keys = definitions[self.kind]
            rows = []
            for row in range(self.rows.rowCount()):
                value = {}
                for col, key in enumerate(keys):
                    cell = self.rows.item(row, col)
                    text = cell.text().strip() if cell else ""
                    if text:
                        value[key] = (
                            _scalar(text) if key in {"lo", "hi", "width", "x", "y"} else text
                        )
                rows.append(value)
            payload[name] = rows
        if self.kind == TABLE:
            columns = [
                self.rows.item(column, 0).text().strip() if self.rows.item(column, 0) else ""
                for column in range(self.rows.rowCount())
            ]
            table_rows = []
            for row in range(self.table_cells.rowCount()):
                value = {}
                for column, key in enumerate(columns):
                    if not key or column >= self.table_cells.columnCount():
                        continue
                    cell = self.table_cells.item(row, column)
                    if cell is None or not cell.text().strip():
                        continue
                    descriptor = cell.data(Qt.UserRole)
                    value[key] = (
                        deepcopy(descriptor)
                        if isinstance(descriptor, dict)
                        else _scalar(cell.text())
                    )
                table_rows.append(value)
            payload["rows"] = table_rows
        return payload

    def _edit_advanced(self):
        current = self._payload()

        def validate(payload):
            if not isinstance(payload, dict):
                return [
                    type(
                        "Issue",
                        (),
                        {"field": "json", "message": "must be an object", "severity": "error"},
                    )()
                ]
            return data_issues(merge_data_payload(self.original, payload))

        dialog = _AdvancedJsonDialog(current, validate, "data element", self)
        if dialog.exec() == QDialog.Accepted:
            self._advanced = dialog.value
            self.status.setText("Advanced JSON accepted; reopen to edit structured rows.")

    def _accept(self):
        candidate = merge_data_payload(self.original, self._payload())
        issues = [issue for issue in data_issues(candidate) if issue.severity == "error"]
        if issues:
            self.status.setText(_messages(issues))
            return
        self._result = candidate
        self.accept()

    def result_data(self):
        return deepcopy(self._result)


class UserEntryDialog(QDialog):
    def __init__(self, entry_data: dict, catalog: BindingCatalog, parent=None):
        super().__init__(parent)
        self.original = deepcopy(entry_data)
        self.catalog = catalog
        self._result = None
        self._result_data = None
        self._advanced = None
        entry = UserEntry.from_dict(entry_data)
        self.setWindowTitle("Configure User Entry")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            lay,
            "Configure user entry",
            "Define the checked operator write, value range and captions.",
        )
        form = QFormLayout()
        lay.addLayout(form)
        self.kind = AuthoringComboBox()
        for value, title in USER_ENTRY_TITLES.items():
            self.kind.addItem(title, value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(entry.kind)))
        form.addRow("Type", self.kind)
        path_row = QWidget()
        path_lay = QHBoxLayout(path_row)
        path_lay.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit(entry.path)
        bind = QPushButton("Bind…")
        bind.clicked.connect(self._bind)
        path_lay.addWidget(self.path, 1)
        path_lay.addWidget(bind)
        form.addRow("Write destination", path_row)
        self.label = QLineEdit(entry.label)
        form.addRow("Label", self.label)
        self.value = QLineEdit(str(entry.value))
        form.addRow("Button value", self.value)
        self.lo = QDoubleSpinBox()
        self.lo.setRange(-1e9, 1e9)
        self.lo.setValue(entry.lo)
        self.hi = QDoubleSpinBox()
        self.hi.setRange(-1e9, 1e9)
        self.hi.setValue(entry.hi)
        form.addRow("Range minimum", self.lo)
        form.addRow("Range maximum", self.hi)
        self.disabled = QLineEdit(entry.disabled_reason)
        form.addRow("Disabled reason", self.disabled)
        lay.addWidget(QLabel("Options (typed value and operator caption)"))
        self.options = QTableWidget(0, 2)
        self.options.setHorizontalHeaderLabels(["Value", "Caption"])
        self.options.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.options, 1)
        for value, caption in entry.options:
            row = self.options.rowCount()
            self.options.insertRow(row)
            self.options.setItem(row, 0, QTableWidgetItem(str(value)))
            self.options.setItem(row, 1, QTableWidgetItem(str(caption)))
        controls = QHBoxLayout()
        add = QPushButton("Add option")
        remove = QPushButton("Remove")
        add.clicked.connect(lambda: self.options.insertRow(self.options.rowCount()))
        remove.clicked.connect(
            lambda: [
                self.options.removeRow(r)
                for r in sorted({i.row() for i in self.options.selectedIndexes()}, reverse=True)
            ]
        )
        controls.addWidget(add)
        controls.addWidget(remove)
        controls.addStretch(1)
        advanced = QPushButton("Advanced JSON…")
        advanced.clicked.connect(self._edit_advanced)
        controls.addWidget(advanced)
        lay.addLayout(controls)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)

    def _bind(self):
        chosen = choose_path(self, self.catalog, self.path.text(), writable=True)
        if chosen is not None:
            self.path.setText(chosen)

    def _accept(self):
        if self._advanced is not None:
            entry = UserEntry.from_dict(self._advanced)
            issues = [issue for issue in user_entry_issues(entry) if issue.severity == "error"]
            if issues:
                self.status.setText(_messages(issues))
                return
            self._result = entry
            self._result_data = deepcopy(self._advanced)
            self.accept()
            return
        options = []
        for row in range(self.options.rowCount()):
            a = self.options.item(row, 0)
            b = self.options.item(row, 1)
            options.append((_scalar(a.text() if a else ""), b.text() if b else ""))
        entry = UserEntry(
            kind=str(self.kind.currentData()),
            path=self.path.text().strip(),
            label=self.label.text().strip(),
            value=_scalar(self.value.text()),
            options=tuple(options),
            lo=self.lo.value(),
            hi=self.hi.value(),
            disabled_reason=self.disabled.text().strip(),
        )
        issues = [i for i in user_entry_issues(entry) if i.severity == "error"]
        if issues:
            self.status.setText(_messages(issues))
            return
        self._result = entry
        candidate = deepcopy(self.original)
        for key in ("kind", "path", "label", "value", "options", "lo", "hi", "disabled_reason"):
            candidate.pop(key, None)
        candidate.update(entry.to_dict())
        self._result_data = candidate
        self.accept()

    def _edit_advanced(self):
        def validate(value):
            if not isinstance(value, dict):
                return [
                    type(
                        "Issue",
                        (),
                        {"field": "json", "message": "must be an object", "severity": "error"},
                    )()
                ]
            return user_entry_issues(UserEntry.from_dict(value))

        dialog = _AdvancedJsonDialog(self.original, validate, "user entry", self)
        if dialog.exec() == QDialog.Accepted:
            self._advanced = dialog.value
            self.status.setText("Advanced JSON accepted.")

    def result_entry(self):
        return self._result

    def result_entry_data(self):
        return deepcopy(self._result_data)


class ActionListDialog(QDialog):
    def __init__(
        self, actions: list[dict], catalog: BindingCatalog, parent=None, *, operator_target=True
    ):
        super().__init__(parent)
        self.original = deepcopy(actions)
        self.catalog = catalog
        self.operator_target = operator_target
        self._advanced = None
        self._result = None
        self.setWindowTitle("Configure Interactions")
        self.resize(800, 480)
        lay = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            lay,
            "Configure interactions",
            "Map supported events to operator-safe display actions.",
        )
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Event", "Action", "Target", "Value", "Active", "Script / procedure context source"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        for raw in actions:
            self._append(Action.from_dict(raw))
        controls = QHBoxLayout()
        add = QPushButton("Add")
        remove = QPushButton("Remove")
        bind = QPushButton("Bind write target…")
        advanced = QPushButton("Advanced JSON…")
        add.clicked.connect(lambda: self._append(Action()))
        remove.clicked.connect(self._remove)
        bind.clicked.connect(self._bind)
        advanced.clicked.connect(self._edit_advanced)
        for b in (add, remove, bind):
            controls.addWidget(b)
        controls.addStretch(1)
        controls.addWidget(advanced)
        lay.addLayout(controls)
        warning = QLabel(
            "Drag is unavailable because the runtime does not dispatch it. Studio-only actions are rejected for operator displays."
        )
        warning.setWordWrap(True)
        lay.addWidget(warning)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        lay.addWidget(buttons)

    def _append(self, action):
        row = self.table.rowCount()
        self.table.insertRow(row)
        event = AuthoringComboBox()
        event.addItems([v for v in MOUSE_EVENTS if v != "drag"])
        event.setCurrentText(action.event)
        kind = AuthoringComboBox()
        kind.addItems(ACTION_KINDS)
        kind.setCurrentText(action.kind)
        self.table.setCellWidget(row, 0, event)
        self.table.setCellWidget(row, 1, kind)
        for col, text in (
            (2, action.target),
            (3, "" if action.value is None else str(action.value)),
            (5, action.source),
        ):
            self.table.setItem(row, col, QTableWidgetItem(text))
        active = QCheckBox()
        active.setChecked(action.active)
        self.table.setCellWidget(row, 4, active)

    def _remove(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def _actions(self):
        out = []
        for row in range(self.table.rowCount()):

            def text(col):
                cell = self.table.item(row, col)
                return cell.text().strip() if cell else ""

            value = text(3)
            out.append(
                Action(
                    event=self.table.cellWidget(row, 0).currentText(),
                    kind=self.table.cellWidget(row, 1).currentText(),
                    target=text(2),
                    value=_scalar(value) if value else None,
                    active=self.table.cellWidget(row, 4).isChecked(),
                    source=text(5),
                )
            )
        return out

    def _bind(self):
        row = self.table.currentRow()
        if row < 0 or self.table.cellWidget(row, 1).currentText() != "write_value":
            return
        chosen = choose_path(self, self.catalog, writable=True)
        if chosen is not None:
            self.table.setItem(row, 2, QTableWidgetItem(chosen))

    def _edit_advanced(self):
        current = merge_actions(self.original, self._actions())

        def validate(value):
            if not isinstance(value, list):
                return [
                    type(
                        "Issue",
                        (),
                        {"field": "json", "message": "must be a list", "severity": "error"},
                    )()
                ]
            issues = []
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    issues.append(
                        type(
                            "Issue",
                            (),
                            {
                                "field": f"[{index}]",
                                "message": "must be an object",
                                "severity": "error",
                            },
                        )()
                    )
                else:
                    issues.extend(
                        action_issues(Action.from_dict(row), operator_target=self.operator_target)
                    )
            return issues

        dialog = _AdvancedJsonDialog(current, validate, "actions", self)
        if dialog.exec() == QDialog.Accepted:
            self._advanced = dialog.value
            self.status.setText("Advanced JSON accepted.")

    def _accept(self):
        rows = (
            self._advanced
            if self._advanced is not None
            else merge_actions(self.original, self._actions())
        )
        errors = []
        for index, row in enumerate(rows):
            for issue in action_issues(Action.from_dict(row), operator_target=self.operator_target):
                if issue.severity == "error":
                    errors.append(
                        type(issue)(
                            f"actions[{index}].{issue.field}", issue.message, issue.severity
                        )
                    )
        if errors:
            self.status.setText(_messages(errors))
            return
        self._result = deepcopy(rows)
        self.accept()

    def result_actions(self):
        return deepcopy(self._result)


__all__ = ["ActionListDialog", "DataElementDialog", "UserEntryDialog", "choose_path"]
