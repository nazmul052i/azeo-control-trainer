"""Control Module Class authoring, instance, and adoption dialogs."""
from __future__ import annotations

import json
from pathlib import Path
import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    ENGINEERING_QSS,
    polish_dialog,
)
from azeo_control_trainer.core.presentation.configuration_chrome import icon
from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.strategy.module_classes import (
    PublicParameter,
    analyze_instance,
    apply_update,
    clear_public_parameter_override,
    create_linked_instance,
    parameter_candidates,
    plan_update,
    project_module_class_library,
    public_parameter_value,
    set_public_parameter_override,
    unlink_instance,
)
from azeo_control_trainer.core.strategy.module_classes.instances import (
    ModuleClassUpdatePlan,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io


_MODULE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= 160 else text[:157] + "..."


def _metric_card(label: str, value: str = "0") -> tuple[QFrame, QLabel]:
    card = QFrame(objectName="metricCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(1)
    number = QLabel(value, objectName="metricValue")
    caption = QLabel(label, objectName="metricLabel")
    layout.addWidget(number)
    layout.addWidget(caption)
    return card, number


def _populate_override_table(table, definition, existing: dict) -> None:
    table.blockSignals(True)
    table.setRowCount(len(definition.public_parameters))
    for row, parameter in enumerate(definition.public_parameters):
        overridden = parameter.name in existing
        check = QTableWidgetItem()
        check.setFlags(check.flags() | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if overridden else Qt.Unchecked)
        check.setToolTip(
            "Checked: this instance owns the value. Unchecked: inherit the "
            "published class default.")
        table.setItem(row, 0, check)
        for column, value in enumerate((
                parameter.name, parameter.data_type,
                _show(parameter.default),
                _show(existing.get(parameter.name, parameter.default))),
                start=1):
            item = QTableWidgetItem(str(value))
            if column < 4 or not overridden:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column == 4 and not overridden:
                item.setForeground(QColor(UI.text_muted))
                item.setToolTip("Inherited from the class; check Override to edit")
            table.setItem(row, column, item)
    table.blockSignals(False)


def _sync_override_editability(table, definition) -> None:
    for row, parameter in enumerate(definition.public_parameters):
        check = table.item(row, 0)
        value = table.item(row, 4)
        if check is None or value is None:
            continue
        enabled = check.checkState() == Qt.Checked
        flags = value.flags()
        value.setFlags(
            flags | Qt.ItemIsEditable if enabled
            else flags & ~Qt.ItemIsEditable)
        value.setForeground(QColor(UI.text) if enabled
                            else QColor(UI.text_muted))
        value.setToolTip(
            "Instance-owned value" if enabled
            else "Inherited from the class; check Override to edit")
        if not enabled:
            value.setText(_show(parameter.default))


def _overrides_from_table(table, definition) -> dict[str, object]:
    from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
        _coerce_public_value,
    )

    result = {}
    for row, parameter in enumerate(definition.public_parameters):
        if table.item(row, 0).checkState() != Qt.Checked:
            continue
        result[parameter.name] = _coerce_public_value(
            table.item(row, 4).text(), parameter.data_type)
    return result


class ModuleClassDefinitionDialog(QDialog):
    """Choose a class identity and its explicit instance properties."""

    def __init__(self, document: dict, definition=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            "Update Control Module Class" if definition
            else "Create Control Module Class")
        self.resize(980, 650)
        self.setMinimumSize(780, 520)
        self.setStyleSheet(ENGINEERING_QSS)
        self._document = document
        self._definition = definition
        self._candidates = parameter_candidates(document)
        self._build_ui()
        self._populate()
        action = "Publish a reviewed successor revision" if definition else \
            "Create a governed master from the active Control Module"
        polish_dialog(
            self,
            title=("Publish Control Module Class Revision" if definition
                   else "Create Control Module Class"),
            subtitle=(f"{action}. Only explicitly exposed properties may "
                      "vary by linked instance."),
            mark="templates",
        )
        self._validate()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)

        metrics = QHBoxLayout()
        source_name = str(self._document.get("name") or "Untitled module")
        for label, value in (
            ("Source module", source_name),
            ("Function blocks", str(len(self._document.get("blocks") or []))),
            ("Connections", str(len(self._document.get("wires") or []))),
            ("Candidate properties", str(len(self._candidates))),
        ):
            card, _value = _metric_card(label, value)
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        identity = QFrame(objectName="configurationInspector")
        identity_layout = QFormLayout(identity)
        identity_layout.setContentsMargins(12, 10, 12, 10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Example: STANDARD_ANALOG_LOOP")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText(
            "Describe the class purpose and approved use")
        identity_layout.addRow("Class name:", self.name_edit)
        identity_layout.addRow("Description:", self.description_edit)
        root.addWidget(identity)

        property_header = QHBoxLayout()
        property_title = QLabel("Public instance properties")
        property_title.setObjectName("configurationObjectTitle")
        property_header.addWidget(property_title)
        property_header.addStretch()
        self.property_search = QLineEdit()
        self.property_search.setPlaceholderText("Find a property…")
        self.property_search.setClearButtonEnabled(True)
        self.property_search.setMaximumWidth(280)
        property_header.addWidget(self.property_search)
        self.exposed_only = QCheckBox("Exposed only")
        property_header.addWidget(self.exposed_only)
        self.public_count = QLabel()
        self.public_count.setObjectName("configurationBadge")
        property_header.addWidget(self.public_count)
        root.addLayout(property_header)
        self.parameters = QTableWidget(0, 6)
        self.parameters.setHorizontalHeaderLabels([
            "Expose", "Public name", "Property path", "Type", "Default",
            "Description"])
        self.parameters.setAlternatingRowColors(True)
        self.parameters.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.parameters.verticalHeader().setVisible(False)
        header = self.parameters.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        root.addWidget(self.parameters, 1)
        inheritance = QLabel(
            "Unchecked properties remain governed by the class. Checked "
            "properties form the typed, reviewable interface for every "
            "linked instance.")
        inheritance.setWordWrap(True)
        inheritance.setStyleSheet(f"color: {UI.text_secondary};")
        root.addWidget(inheritance)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setObjectName("stateBad")
        root.addWidget(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText(
            "Publish Revision" if self._definition else "Create Class")
        self.save_button.setObjectName("primary")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.name_edit.textChanged.connect(self._validate)
        self.property_search.textChanged.connect(self._apply_parameter_filter)
        self.exposed_only.toggled.connect(self._apply_parameter_filter)
        self.parameters.itemChanged.connect(self._parameter_changed)

    def _populate(self):
        definition = self._definition
        self.name_edit.setText(
            definition.name if definition
            else str(self._document.get("name") or "MODULE_CLASS"))
        self.description_edit.setText(
            definition.description if definition else "")
        declared = ({item.path.casefold(): item
                     for item in definition.public_parameters}
                    if definition else {})
        candidates = list(self._candidates)
        candidate_paths = {item.path.casefold() for item in candidates}
        if definition:
            for item in definition.public_parameters:
                if item.path.casefold() not in candidate_paths:
                    from azeo_control_trainer.core.strategy.module_classes.instances import (
                        ParameterCandidate,
                    )
                    candidates.append(ParameterCandidate(
                        item.name, item.path, item.data_type,
                        item.default, item.description))
        self.parameters.setRowCount(len(candidates))
        self.parameters.blockSignals(True)
        for row, candidate in enumerate(candidates):
            existing = declared.get(candidate.path.casefold())
            exposed = QTableWidgetItem()
            exposed.setFlags(exposed.flags() | Qt.ItemIsUserCheckable)
            exposed.setCheckState(Qt.Checked if existing else Qt.Unchecked)
            self.parameters.setItem(row, 0, exposed)
            values = (
                existing.name if existing else candidate.name,
                candidate.path,
                existing.data_type if existing else candidate.data_type,
                _show(existing.default if existing else candidate.default),
                existing.description if existing else candidate.description,
            )
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(str(value))
                if column in {2, 3}:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.parameters.setItem(row, column, cell)
            self._sync_parameter_row(row)
        self.parameters.blockSignals(False)
        self._apply_parameter_filter()

    def _sync_parameter_row(self, row: int) -> None:
        exposed = self.parameters.item(row, 0)
        enabled = bool(exposed and exposed.checkState() == Qt.Checked)
        for column in (1, 4, 5):
            cell = self.parameters.item(row, column)
            if cell is None:
                continue
            flags = cell.flags()
            cell.setFlags((flags | Qt.ItemIsEditable) if enabled
                          else (flags & ~Qt.ItemIsEditable))
            cell.setForeground(QColor(UI.text) if enabled
                               else QColor(UI.text_muted))
        for column in (2, 3):
            cell = self.parameters.item(row, column)
            if cell is not None:
                cell.setForeground(QColor(UI.text_secondary))

    def _parameter_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._sync_parameter_row(item.row())
        self._apply_parameter_filter()
        self._validate()

    def _apply_parameter_filter(self) -> None:
        needle = self.property_search.text().strip().casefold()
        exposed_only = self.exposed_only.isChecked()
        exposed_count = 0
        visible_count = 0
        for row in range(self.parameters.rowCount()):
            checked = self.parameters.item(row, 0).checkState() == Qt.Checked
            exposed_count += int(checked)
            haystack = " ".join(
                self.parameters.item(row, column).text()
                for column in range(1, self.parameters.columnCount())
                if self.parameters.item(row, column) is not None
            ).casefold()
            visible = (not needle or needle in haystack) and (
                not exposed_only or checked)
            self.parameters.setRowHidden(row, not visible)
            visible_count += int(visible)
        suffix = (f" · {visible_count} shown"
                  if visible_count != self.parameters.rowCount() else "")
        self.public_count.setText(
            f"{exposed_count} exposed{suffix}")

    def _validation_error(self) -> str:
        if not self.name_edit.text().strip():
            return "Enter a class name."
        try:
            parameters = self.declared_parameters()
        except (TypeError, ValueError) as exc:
            return str(exc)
        names = [item.name.casefold() for item in parameters]
        if any(not name for name in names):
            return "Every exposed property needs a public name."
        if len(names) != len(set(names)):
            return "Public property names must be unique."
        return ""

    def _validate(self, *_args) -> None:
        error = self._validation_error()
        self._error.setText(error)
        self.save_button.setEnabled(not error)

    def declared_parameters(self) -> list[PublicParameter]:
        from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
            _coerce_public_value,
        )

        result = []
        for row in range(self.parameters.rowCount()):
            exposed = self.parameters.item(row, 0)
            if exposed is None or exposed.checkState() != Qt.Checked:
                continue
            values = [self.parameters.item(row, column).text().strip()
                      for column in range(1, 6)]
            name, path, data_type, raw_default, description = values
            result.append(PublicParameter(
                name=name,
                path=path,
                data_type=data_type.upper() or "ANY",
                default=_coerce_public_value(raw_default, data_type),
                description=description,
            ))
        return result

    def _accept_checked(self):
        error = self._validation_error()
        if error:
            self._error.setText(error)
            return
        self.accept()


class ModuleInstanceOverridesDialog(QDialog):
    """Edit only the properties deliberately exposed by the class."""

    def __init__(self, definition, document=None, parent=None):
        super().__init__(parent)
        self.definition = definition
        self.document = document
        self.setWindowTitle("Control Module Instance Properties")
        self.resize(820, 500)
        self.setStyleSheet(ENGINEERING_QSS)
        root = QVBoxLayout(self)
        summary = QFrame(objectName="configurationInspector")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(12, 8, 12, 8)
        summary_layout.addWidget(QLabel(
            f"{definition.name}  ·  revision {definition.revision}"))
        summary_layout.addStretch()
        inherited = QLabel("Unchecked = inherited")
        inherited.setObjectName("configurationBadge")
        summary_layout.addWidget(inherited)
        root.addWidget(summary)
        self.table = QTableWidget(len(definition.public_parameters), 5)
        self.table.setHorizontalHeaderLabels([
            "Override", "Property", "Type", "Inherited", "Instance value"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        raw_link = ((document or {}).get("module_class") or {})
        existing = dict(raw_link.get("public_parameter_overrides", {}))
        _populate_override_table(self.table, definition, existing)
        self.table.itemChanged.connect(self._item_changed)
        root.addWidget(self.table, 1)
        note = QLabel(
            "Select Override only for values intentionally owned by this "
            "instance. Clearing it immediately restores class inheritance.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {UI.text_secondary};")
        root.addWidget(note)
        self._error = QLabel("")
        self._error.setObjectName("stateBad")
        root.addWidget(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply Properties")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        polish_dialog(
            self,
            title="Instance Properties",
            subtitle=("Review the typed contract between this linked module "
                      "and its governing class."),
            mark="params",
        )

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.table.blockSignals(True)
            _sync_override_editability(self.table, self.definition)
            self.table.blockSignals(False)
        self._error.clear()

    def overrides(self) -> dict[str, object]:
        return _overrides_from_table(self.table, self.definition)

    def _accept_checked(self):
        try:
            self.overrides()
        except (TypeError, ValueError) as exc:
            self._error.setText(str(exc))
            return
        self.accept()


class ModuleInstanceCreationDialog(QDialog):
    """Create and place a linked instance in one reviewable transaction."""

    def __init__(self, definition, areas, existing_names=(), parent=None):
        super().__init__(parent)
        self.definition = definition
        self._areas = [dict(area) for area in (areas or [])]
        self._existing_names = {
            str(name).strip().casefold() for name in existing_names
            if str(name).strip()
        }
        self.setWindowTitle("Create Linked Control Module")
        self.resize(900, 650)
        self.setMinimumSize(760, 560)
        self.setStyleSheet(ENGINEERING_QSS)
        self._build_ui()
        self._populate_areas()
        polish_dialog(
            self,
            title="Create Linked Control Module",
            subtitle=(f"Instantiate {definition.name} revision "
                      f"{definition.revision}, place it in the project, and "
                      "review its approved instance properties."),
            mark="new",
        )
        self._validate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)

        metrics = QHBoxLayout()
        for label, value in (
            ("Governing class", self.definition.name),
            ("Revision", f"r{self.definition.revision}"),
            ("Public properties", str(len(self.definition.public_parameters))),
        ):
            card, _value = _metric_card(label, value)
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        placement = QFrame(objectName="configurationInspector")
        form = QFormLayout(placement)
        form.setContentsMargins(12, 10, 12, 10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Example: FIC-202")
        self.area_combo = QComboBox()
        self.unit_combo = QComboBox()
        self.destination = QLabel()
        self.destination.setStyleSheet(f"color: {UI.text_secondary};")
        form.addRow("Module name:", self.name_edit)
        form.addRow("Project area:", self.area_combo)
        form.addRow("Process unit:", self.unit_combo)
        form.addRow("Document:", self.destination)
        root.addWidget(placement)

        title_row = QHBoxLayout()
        title = QLabel("Initial instance properties")
        title.setObjectName("configurationObjectTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        badge = QLabel("Unchecked values inherit")
        badge.setObjectName("configurationBadge")
        title_row.addWidget(badge)
        root.addLayout(title_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Override", "Property", "Type", "Class default", "Instance value"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        _populate_override_table(self.table, self.definition, {})
        root.addWidget(self.table, 1)

        self._error = QLabel()
        self._error.setObjectName("stateBad")
        self._error.setWordWrap(True)
        root.addWidget(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.create_button = buttons.button(QDialogButtonBox.Ok)
        self.create_button.setText("Create Linked Module")
        self.create_button.setObjectName("primary")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.name_edit.textChanged.connect(self._validate)
        self.area_combo.currentIndexChanged.connect(self._area_changed)
        self.unit_combo.currentIndexChanged.connect(self._validate)
        self.table.itemChanged.connect(self._override_changed)

    def _populate_areas(self) -> None:
        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        if not self._areas:
            self.area_combo.addItem("Project default", "")
        else:
            for area in self._areas:
                label = str(area.get("name") or area.get("area_id") or "Area")
                self.area_combo.addItem(label, str(area.get("area_id") or ""))
        self.area_combo.blockSignals(False)
        self._area_changed()

    def _selected_area(self) -> dict:
        area_id = str(self.area_combo.currentData() or "")
        return next((area for area in self._areas
                     if str(area.get("area_id") or "") == area_id), {})

    def _area_changed(self, *_args) -> None:
        selected = self.unit_name()
        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        self.unit_combo.addItem("Unassigned", "")
        for unit in self._selected_area().get("units") or []:
            if not isinstance(unit, dict):
                continue
            name = str(unit.get("name") or "").strip()
            if name:
                self.unit_combo.addItem(name, name)
        match = self.unit_combo.findData(selected)
        self.unit_combo.setCurrentIndex(max(0, match))
        self.unit_combo.blockSignals(False)
        self._validate()

    def _override_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.table.blockSignals(True)
            _sync_override_editability(self.table, self.definition)
            self.table.blockSignals(False)
        self._validate()

    def module_name(self) -> str:
        return self.name_edit.text().strip()

    def area_id(self) -> str | None:
        return str(self.area_combo.currentData() or "") or None

    def unit_name(self) -> str | None:
        return str(self.unit_combo.currentData() or "") or None

    def overrides(self) -> dict[str, object]:
        return _overrides_from_table(self.table, self.definition)

    def _validation_error(self) -> str:
        name = self.module_name()
        if not _MODULE_NAME.fullmatch(name):
            return ("Module name must start with a letter and use only "
                    "letters, numbers, dot, underscore, or hyphen.")
        if name.casefold() in self._existing_names:
            return f"A project module named {name!r} already exists."
        try:
            self.overrides()
        except (TypeError, ValueError) as exc:
            return str(exc)
        return ""

    def _validate(self, *_args) -> None:
        name = self.module_name()
        self.destination.setText(
            f"control/{name or '<module-name>'}.json")
        error = self._validation_error()
        self._error.setText(error)
        self.create_button.setEnabled(not error)

    def _accept_checked(self) -> None:
        error = self._validation_error()
        if error:
            self._error.setText(error)
            return
        self.accept()


class ModuleClassUpdateReviewDialog(QDialog):
    """Make class adoption and local-deviation loss explicit."""

    ADOPT = 2
    PRESERVE = 3

    def __init__(self, plan: ModuleClassUpdatePlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.choice = 0
        self.setWindowTitle(f"Review Class Update — {plan.module}")
        self.resize(1040, 650)
        self.setMinimumSize(820, 520)
        self.setStyleSheet(ENGINEERING_QSS)
        root = QVBoxLayout(self)
        metrics = QHBoxLayout()
        for label, value in (
            ("Class changes", str(len(plan.class_changes))),
            ("Instance deviations", str(len(plan.instance_deviations))),
            ("Conflicts", str(len(plan.conflict_paths))),
        ):
            card, _value = _metric_card(label, value)
            metrics.addWidget(card, 1)
        root.addLayout(metrics)
        tabs = QTabWidget()
        tabs.addTab(
            self._change_table(plan.class_changes),
            f"Class Changes ({len(plan.class_changes)})")
        tabs.addTab(
            self._change_table(plan.instance_deviations),
            f"Instance Deviations ({len(plan.instance_deviations)})")
        conflict = QTableWidget(len(plan.conflict_paths), 1)
        conflict.setHorizontalHeaderLabels(["Conflicting property path"])
        conflict.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        conflict.verticalHeader().setVisible(False)
        for row, path in enumerate(plan.conflict_paths):
            conflict.setItem(row, 0, QTableWidgetItem(path))
        tabs.addTab(conflict, f"Conflicts ({len(plan.conflict_paths)})")
        root.addWidget(tabs, 1)
        warning_bar = QFrame(objectName="warningBar")
        warning_layout = QHBoxLayout(warning_bar)
        warning_layout.setContentsMargins(10, 7, 10, 7)
        warning = QLabel(
            "Adopt Class discards all direct instance deviations but retains "
            "declared property overrides. Preserve is available only when the "
            "class and instance did not edit the same property.")
        warning.setWordWrap(True)
        warning_layout.addWidget(warning)
        root.addWidget(warning_bar)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        adopt = QPushButton("Adopt Class")
        adopt.setObjectName("primary")
        adopt.clicked.connect(lambda: self._choose(self.ADOPT))
        buttons.addButton(adopt, QDialogButtonBox.AcceptRole)
        preserve = QPushButton("Preserve Non-conflicting Deviations")
        preserve.setEnabled(not plan.has_conflicts)
        preserve.clicked.connect(lambda: self._choose(self.PRESERVE))
        buttons.addButton(preserve, QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        polish_dialog(
            self,
            title=f"Review {plan.definition.name} Revision {plan.definition.revision}",
            subtitle=(f"Compare the active {plan.module} instance with its "
                      "governing class before choosing an adoption policy."),
            mark="compare",
        )

    @staticmethod
    def _change_table(changes):
        table = QTableWidget(len(changes), 5)
        table.setHorizontalHeaderLabels([
            "Scope", "Action", "Object", "Difference", "Before → After"])
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for row, change in enumerate(changes):
            values = (
                change.scope.title(), change.action.title(), change.label,
                change.detail, f"{_show(change.before)} → {_show(change.after)}")
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        return table

    def _choose(self, choice):
        self.choice = choice
        self.accept()


class ModuleClassManagerDialog(QDialog):
    """Manage class definitions and the active linked module instance."""

    documentRequested = Signal(object, object, str)  # canvas, document, reason
    modulesCreated = Signal(object)  # list[Path]

    def __init__(
        self, project_tree, canvases, active_canvas=None, library=None,
        parent=None,
    ):
        super().__init__(parent)
        self.project_tree = project_tree
        self.canvases = list(canvases)
        self.active_canvas = active_canvas
        self.library = library or project_module_class_library()
        self._document_cache = None
        self.setWindowTitle("Control Module Classes")
        self.resize(1220, 780)
        self.setMinimumSize(940, 640)
        self.setStyleSheet(ENGINEERING_QSS)
        self._build_ui()
        self.reload()
        polish_dialog(
            self,
            title="Control Module Class Engineering",
            subtitle=("Create governed masters, place linked instances, and "
                      "review every revision or local deviation explicitly."),
            mark="templates",
        )

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)

        metrics = QHBoxLayout()
        cards = (
            ("Published classes", "class_count_metric"),
            ("Linked instances", "instance_count_metric"),
            ("Stale instances", "stale_count_metric"),
            ("Active module", "active_state_metric"),
        )
        for label, attribute in cards:
            card, value = _metric_card(label)
            setattr(self, attribute, value)
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_title = QLabel("Class library")
        left_title.setObjectName("configurationObjectTitle")
        left_layout.addWidget(left_title)
        self.class_search = QLineEdit()
        self.class_search.setPlaceholderText("Find a class…")
        self.class_search.setClearButtonEnabled(True)
        self.class_search.textChanged.connect(self._filter_classes)
        left_layout.addWidget(self.class_search)
        self.classes = QListWidget()
        self.classes.setIconSize(QSize(20, 20))
        self.classes.setSpacing(2)
        self.classes.currentRowChanged.connect(self._show_selected)
        left_layout.addWidget(self.classes, 1)
        self.class_summary = QLabel()
        self.class_summary.setWordWrap(True)
        self.class_summary.setObjectName("configurationSubtitle")
        left_layout.addWidget(self.class_summary)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        active_card = QFrame(objectName="configurationInspector")
        active_layout = QHBoxLayout(active_card)
        active_layout.setContentsMargins(12, 9, 12, 9)
        active_label = QLabel("ACTIVE MODULE")
        active_label.setObjectName("configurationFieldLabel")
        active_layout.addWidget(active_label)
        self.active_summary = QLabel()
        self.active_summary.setWordWrap(True)
        active_layout.addWidget(self.active_summary, 1)
        right_layout.addWidget(active_card)
        self.tabs = QTabWidget()
        self.parameters = QTableWidget(0, 5)
        self.parameters.setHorizontalHeaderLabels([
            "Property", "Path", "Type", "Class default", "Active instance"])
        self._table(self.parameters, stretch=(1, 4))
        self.tabs.addTab(self.parameters, "Public Properties (0)")
        self.instances = QTableWidget(0, 5)
        self.instances.setHorizontalHeaderLabels([
            "Module", "State", "Revision", "Overrides", "Deviations"])
        self._table(self.instances, stretch=(0,))
        self.tabs.addTab(self.instances, "Instances (0)")
        self.deviations = QTableWidget(0, 4)
        self.deviations.setHorizontalHeaderLabels([
            "Scope", "Object", "Difference", "Before → After"])
        self._table(self.deviations, stretch=(2, 3))
        self.tabs.addTab(self.deviations, "Active Deviations (0)")
        self.revisions = QTableWidget(0, 4)
        self.revisions.setHorizontalHeaderLabels([
            "Revision", "Updated", "Digest", "State"])
        self._table(self.revisions, stretch=(1, 2))
        self.tabs.addTab(self.revisions, "Class History (0)")
        right_layout.addWidget(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setSizes([290, 800])
        root.addWidget(splitter, 1)

        self.create_button = QPushButton("Create Class from Active")
        self.update_button = QPushButton("Publish Active as New Revision")
        self.instance_button = QPushButton("Create Linked Instance")
        self.overrides_button = QPushButton("Instance Properties")
        self.refresh_button = QPushButton("Review / Adopt Update")
        self.unlink_button = QPushButton("Unlink Active")
        self.delete_button = QPushButton("Delete Unused Class")
        self.create_button.setProperty("configurationPrimary", True)
        self.delete_button.setProperty("configurationQuiet", True)
        action_specs = {
            self.create_button: self._create_clicked,
            self.update_button: self._update_clicked,
            self.instance_button: self._instance_clicked,
            self.overrides_button: self._overrides_clicked,
            self.refresh_button: self._refresh_clicked,
            self.unlink_button: self._unlink_clicked,
            self.delete_button: self._delete_clicked,
        }
        for button, callback in action_specs.items():
            button.clicked.connect(callback)

        actions = QHBoxLayout()
        class_actions = QFrame(objectName="configurationInspector")
        class_layout = QVBoxLayout(class_actions)
        class_layout.setContentsMargins(10, 7, 10, 7)
        class_title = QLabel("CLASS LIFECYCLE")
        class_title.setObjectName("configurationFieldLabel")
        class_layout.addWidget(class_title)
        class_buttons = QHBoxLayout()
        class_buttons.addWidget(self.create_button)
        class_buttons.addWidget(self.update_button)
        class_buttons.addWidget(self.instance_button)
        class_layout.addLayout(class_buttons)
        actions.addWidget(class_actions, 1)

        instance_actions = QFrame(objectName="configurationInspector")
        instance_layout = QVBoxLayout(instance_actions)
        instance_layout.setContentsMargins(10, 7, 10, 7)
        instance_title = QLabel("ACTIVE INSTANCE")
        instance_title.setObjectName("configurationFieldLabel")
        instance_layout.addWidget(instance_title)
        instance_buttons = QHBoxLayout()
        instance_buttons.addWidget(self.overrides_button)
        instance_buttons.addWidget(self.refresh_button)
        instance_buttons.addWidget(self.unlink_button)
        instance_layout.addLayout(instance_buttons)
        actions.addWidget(instance_actions, 1)
        root.addLayout(actions)

        maintenance = QHBoxLayout()
        maintenance.addWidget(self.delete_button)
        maintenance.addStretch()
        root.addLayout(maintenance)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.create_button.setToolTip(
            "Publish the active independent module as revision 1 and link it")
        self.update_button.setToolTip(
            "Publish the active current instance as the next class revision")
        self.instance_button.setToolTip(
            "Create, place, and configure a new linked Control Module")
        self.overrides_button.setToolTip(
            "Edit only properties exposed by the governing class")
        self.refresh_button.setToolTip(
            "Compare the stale instance with the current class before adoption")
        self.unlink_button.setToolTip(
            "Keep effective logic but end the module's class relationship")
        self.delete_button.setToolTip(
            "Delete the selected class only when no linked instances remain")

    @staticmethod
    def _table(table, *, stretch=()):
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        for column in range(table.columnCount()):
            table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.Stretch if column in stretch
                else QHeaderView.ResizeToContents)

    def _active_document(self) -> dict | None:
        if self.active_canvas is None:
            return None
        document = self.active_canvas.scene.graph.to_dict()
        comments = self.active_canvas.scene.get_comments_data()
        if comments:
            document["comments"] = comments
        return document

    def _ensure_active_offline(self) -> None:
        runtime = getattr(self.active_canvas, "runtime", None)
        if runtime is not None and getattr(runtime, "is_online", False):
            raise ValueError(
                "Take the active module offline before changing its class "
                "link, revision, or instance properties")

    def _selected_definition(self):
        item = self.classes.currentItem()
        if item is None:
            return None
        try:
            return self.library.get(str(item.data(Qt.UserRole)))
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _module_documents(self) -> list[tuple[Path | None, dict]]:
        result: dict[Path, dict] = {}
        for _folder, paths in strategy_io.list_strategy_folders().items():
            for path in paths:
                resolved = Path(path).resolve()
                try:
                    result[resolved] = json.loads(
                        resolved.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        detached = []
        for canvas in self.canvases:
            if canvas.file_path:
                result[Path(canvas.file_path).resolve()] = \
                    canvas.scene.graph.to_dict()
            else:
                detached.append((None, canvas.scene.graph.to_dict()))
        return list(result.items()) + detached

    def _instances_for(self, definition_id: str):
        rows = []
        documents = (self._document_cache if self._document_cache is not None
                     else self._module_documents())
        for path, document in documents:
            raw = document.get("module_class") or {}
            if raw.get("definition_id") != definition_id:
                continue
            try:
                status = analyze_instance(document, self.library)
            except (TypeError, ValueError):
                continue
            rows.append((path, document, status))
        return rows

    def _filter_classes(self, text: str = "") -> None:
        needle = str(text).strip().casefold()
        first_visible = -1
        current_visible = False
        for row in range(self.classes.count()):
            item = self.classes.item(row)
            visible = not needle or needle in (
                f"{item.text()} {item.toolTip()}".casefold())
            item.setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row
            if visible and item is self.classes.currentItem():
                current_visible = True
        if not current_visible and first_visible >= 0:
            self.classes.setCurrentRow(first_visible)

    @staticmethod
    def _set_action_state(button: QPushButton, enabled: bool, reason: str) -> None:
        base = str(button.toolTip()).split("\n", 1)[0]
        button.setEnabled(enabled)
        if enabled:
            button.setToolTip(base)
            return
        button.setToolTip(f"{base}\nUnavailable: {reason}")

    def reload(self):
        selected = (self.classes.currentItem().data(Qt.UserRole)
                    if self.classes.currentItem() else None)
        definitions = self.library.list()
        self._document_cache = self._module_documents()
        instance_rows = {
            definition.id: self._instances_for(definition.id)
            for definition in definitions
        }
        self.classes.clear()
        for definition in definitions:
            rows = instance_rows[definition.id]
            count = len(rows)
            stale = sum(status.state == "stale"
                        for _path, _document, status in rows)
            item = QListWidgetItem(
                icon("templates"),
                f"{definition.name}\nRevision {definition.revision}  ·  "
                f"{count} instance(s)" + (f"  ·  {stale} stale" if stale else ""))
            item.setSizeHint(QSize(210, 50))
            item.setData(Qt.UserRole, definition.id)
            item.setToolTip(
                f"{definition.description or 'No description'}\n"
                f"Digest {definition.digest[:16]}")
            self.classes.addItem(item)
        all_rows = [row for rows in instance_rows.values() for row in rows]
        self.class_count_metric.setText(str(len(definitions)))
        self.instance_count_metric.setText(str(len(all_rows)))
        self.stale_count_metric.setText(str(sum(
            status.state == "stale" for _path, _document, status in all_rows)))
        index = next((row for row in range(self.classes.count())
                      if self.classes.item(row).data(Qt.UserRole) == selected),
                     0 if self.classes.count() else -1)
        if index >= 0:
            self.classes.setCurrentRow(index)
        else:
            self._show_selected(-1)
        self._filter_classes(self.class_search.text())

    def _show_selected(self, _row):
        definition = self._selected_definition()
        active = self._active_document()
        active_status = None
        if active is not None:
            try:
                active_status = analyze_instance(active, self.library)
            except (TypeError, ValueError):
                active_status = None
        if definition is None:
            self.class_summary.setText("No module classes are published.")
            self.parameters.setRowCount(0)
            self.instances.setRowCount(0)
            self.revisions.setRowCount(0)
            self.tabs.setTabText(0, "Public Properties (0)")
            self.tabs.setTabText(1, "Instances (0)")
            self.tabs.setTabText(3, "Class History (0)")
        else:
            rows = self._instances_for(definition.id)
            self.class_summary.setText(
                f"{definition.description or 'No description'}\n"
                f"r{definition.revision} · digest {definition.digest[:12]} · "
                f"{len(definition.public_parameters)} public property(s)")
            self._show_parameters(definition, active)
            self._show_instances(rows)
            self._show_revisions(definition)
            self.tabs.setTabText(
                0, f"Public Properties ({len(definition.public_parameters)})")
            self.tabs.setTabText(1, f"Instances ({len(rows)})")
            self.tabs.setTabText(
                3, f"Class History ({len(self.library.revisions(definition.id))})")
        self._show_active(active_status)
        selected_id = definition.id if definition else ""
        active_matches = bool(
            active_status and active_status.linked
            and active_status.definition_id == selected_id)
        self._set_action_state(
            self.create_button,
            active is not None and not (active_status and active_status.linked),
            "open an independent Control Module")
        active_current = bool(active_matches and active_status.state == "current")
        self._set_action_state(
            self.update_button, active_current,
            "select the class of an active current instance")
        self._set_action_state(
            self.instance_button, definition is not None,
            "select a published class")
        self._set_action_state(
            self.overrides_button, active_current,
            "open a current instance of the selected class")
        self._set_action_state(
            self.refresh_button,
            bool(active_matches and active_status.state == "stale"),
            "open a stale instance of the selected class")
        self._set_action_state(
            self.unlink_button, active_matches,
            "open an instance of the selected class")
        count = len(self._instances_for(selected_id)) if selected_id else 0
        self._set_action_state(
            self.delete_button, definition is not None and count == 0,
            "the class still has linked instances")

    def _show_parameters(self, definition, active):
        self.parameters.setRowCount(len(definition.public_parameters))
        active_link = (active or {}).get("module_class") or {}
        matches = active_link.get("definition_id") == definition.id
        for row, parameter in enumerate(definition.public_parameters):
            effective = "—"
            if matches:
                try:
                    value = public_parameter_value(active, parameter.name)
                    overridden = parameter.name in active_link.get(
                        "public_parameter_overrides", {})
                    effective = f"{_show(value)} ({'override' if overridden else 'inherited'})"
                except (TypeError, ValueError, KeyError):
                    effective = "Invalid"
            values = (
                parameter.name, parameter.path, parameter.data_type,
                _show(parameter.default), effective)
            for column, value in enumerate(values):
                self.parameters.setItem(row, column, QTableWidgetItem(value))

    def _show_instances(self, rows):
        self.instances.setRowCount(len(rows))
        for row, (_path, _document, status) in enumerate(rows):
            values = (
                status.module, status.state.title(),
                f"r{status.instance_revision} / r{status.library_revision}",
                str(status.overrides), str(len(status.deviations)))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    colors = {
                        "current": UI.success,
                        "stale": "#9A6700",
                        "missing": UI.error,
                    }
                    item.setForeground(QColor(colors.get(
                        status.state, UI.text_secondary)))
                self.instances.setItem(row, column, item)

    def _show_revisions(self, definition):
        records = [self.library.get_revision(definition.id, revision)
                   for revision in self.library.revisions(definition.id)]
        self.revisions.setRowCount(len(records))
        for row, record in enumerate(reversed(records)):
            values = (
                f"r{record.revision}", record.updated_at,
                record.digest[:16],
                "Current" if record.revision == definition.revision
                else "Archived",
            )
            for column, value in enumerate(values):
                self.revisions.setItem(row, column, QTableWidgetItem(value))

    def _show_active(self, status):
        if self.active_canvas is None:
            self.active_summary.setText("No active Control Module.")
            self.active_state_metric.setText("None")
            self.deviations.setRowCount(0)
            self.tabs.setTabText(2, "Active Deviations (0)")
            return
        if status is None:
            self.active_summary.setText("Active module has invalid class metadata.")
            self.active_state_metric.setText("Invalid")
            self.deviations.setRowCount(0)
            self.tabs.setTabText(2, "Active Deviations (0)")
            return
        if not status.linked:
            self.active_summary.setText(
                f"{status.module} · INDEPENDENT · no governing class")
            self.active_state_metric.setText("Independent")
            self.deviations.setRowCount(0)
            self.tabs.setTabText(2, "Active Deviations (0)")
            return
        self.active_state_metric.setText(status.state.title())
        self.active_summary.setText(
            f"{status.module} · {status.definition_name} · "
            f"{status.state.upper()} · instance r{status.instance_revision} / "
            f"library r{status.library_revision} · {status.overrides} override(s) · "
            f"{len(status.deviations)} deviation(s)")
        self.deviations.setRowCount(len(status.deviations))
        self.tabs.setTabText(
            2, f"Active Deviations ({len(status.deviations)})")
        for row, change in enumerate(status.deviations):
            values = (
                change.scope.title(), change.label, change.detail,
                f"{_show(change.before)} → {_show(change.after)}")
            for column, value in enumerate(values):
                self.deviations.setItem(row, column, QTableWidgetItem(value))

    def create_from_active(
        self, name: str, description: str = "",
        public_parameters: list[PublicParameter] | None = None,
    ):
        document = self._active_document()
        if document is None:
            raise ValueError("Open a Control Module first")
        self._ensure_active_offline()
        if document.get("module_class"):
            raise ValueError("Active module is already linked to a class")
        definition = self.library.create(
            name, document, description=description,
            public_parameters=public_parameters or [])
        linked = create_linked_instance(
            definition, str(document.get("name") or name))
        self.documentRequested.emit(
            self.active_canvas, linked, f"Link to class {definition.name}")
        self.reload()
        return definition

    def update_from_active(
        self, *, name: str | None = None, description: str | None = None,
        public_parameters: list[PublicParameter] | None = None,
        note: str = "Reviewed class revision",
    ):
        definition = self._selected_definition()
        document = self._active_document()
        if definition is None or document is None:
            raise ValueError("Select a class and active linked module")
        raw = document.get("module_class") or {}
        if raw.get("definition_id") != definition.id:
            raise ValueError("Active module is not an instance of this class")
        if (raw.get("definition_revision") != definition.revision
                or raw.get("definition_digest") != definition.digest):
            raise ValueError(
                "Adopt the current class revision before publishing a "
                "successor from this instance")
        updated = self.library.update(
            definition.id,
            expected_revision=definition.revision,
            graph=document,
            name=name,
            description=description,
            public_parameters=public_parameters,
            note=note,
        )
        self.reload()
        return updated

    def create_instance(
        self, module_name: str, *, overrides: dict | None = None,
        area_id: str | None = None, unit_name: str | None = None,
        target_directory: Path | None = None,
    ) -> Path:
        definition = self._selected_definition()
        if definition is None:
            raise ValueError("Select a module class")
        name = str(module_name).strip()
        if not _MODULE_NAME.fullmatch(name):
            raise ValueError(
                "Module name must start with a letter and contain only "
                "letters, numbers, dot, underscore, or hyphen")
        if any(str(document.get("name") or "").casefold() == name.casefold()
               for _path, document in self._module_documents()):
            raise ValueError(f"A project module named {name!r} already exists")
        if target_directory is None:
            active_path = getattr(self.active_canvas, "file_path", None)
            preferred = strategy_io.STRATEGY_DIR / "control"
            target_directory = (preferred if preferred.exists()
                                else Path(active_path).parent if active_path
                                else strategy_io.STRATEGY_DIR)
        target = Path(target_directory).resolve() / f"{name}.json"
        if target.exists():
            raise FileExistsError(f"Module already exists: {target.name}")
        document = create_linked_instance(
            definition, name, overrides=overrides or {})
        strategy_io.write_json_transactional(target, document)
        try:
            if unit_name:
                self.project_tree.register_control_modules(
                    [target], area_id, unit_name)
            else:
                self.project_tree.register_control_modules([target], area_id)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.project_tree.refresh()
        self.modulesCreated.emit([target])
        self.reload()
        return target

    def set_active_overrides(self, overrides: dict[str, object]) -> dict:
        definition = self._selected_definition()
        document = self._active_document()
        if definition is None or document is None:
            raise ValueError("Select a class and active instance")
        self._ensure_active_offline()
        raw = document.get("module_class") or {}
        if raw.get("definition_id") != definition.id:
            raise ValueError("Active module is not an instance of this class")
        if (raw.get("definition_revision") != definition.revision
                or raw.get("definition_digest") != definition.digest):
            raise ValueError(
                "Adopt the current class revision before editing instance "
                "properties")
        result = document
        for parameter in definition.public_parameters:
            if parameter.name in overrides:
                result = set_public_parameter_override(
                    result, parameter.name, overrides[parameter.name])
            else:
                result = clear_public_parameter_override(
                    result, parameter.name)
        self.documentRequested.emit(
            self.active_canvas, result, "Change class instance properties")
        self.reload()
        return result

    def update_plan(self) -> ModuleClassUpdatePlan:
        definition = self._selected_definition()
        document = self._active_document()
        if definition is None or document is None:
            raise ValueError("Select a class and active instance")
        return plan_update(document, definition)

    def refresh_active(self, *, preserve_deviations: bool = False) -> dict:
        definition = self._selected_definition()
        document = self._active_document()
        if definition is None or document is None:
            raise ValueError("Select a class and active instance")
        self._ensure_active_offline()
        updated = apply_update(
            document, definition,
            preserve_deviations=preserve_deviations)
        self.documentRequested.emit(
            self.active_canvas, updated,
            f"Adopt {definition.name} r{definition.revision}")
        self.reload()
        return updated

    def unlink_active(self) -> dict:
        document = self._active_document()
        if document is None:
            raise ValueError("Open a linked module first")
        self._ensure_active_offline()
        independent = unlink_instance(document)
        self.documentRequested.emit(
            self.active_canvas, independent, "Unlink module class")
        self.reload()
        return independent

    def _create_clicked(self):
        document = self._active_document()
        if document is None or is_headless():
            return
        dialog = ModuleClassDefinitionDialog(document, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.create_from_active(
                dialog.name_edit.text(), dialog.description_edit.text(),
                dialog.declared_parameters())
        except Exception as exc:
            self._error("Create Module Class", exc)

    def _update_clicked(self):
        document = self._active_document()
        definition = self._selected_definition()
        if document is None or definition is None or is_headless():
            return
        dialog = ModuleClassDefinitionDialog(
            document, definition=definition, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.update_from_active(
                name=dialog.name_edit.text(),
                description=dialog.description_edit.text(),
                public_parameters=dialog.declared_parameters())
        except Exception as exc:
            self._error("Publish Module Class Revision", exc)

    def _instance_clicked(self):
        definition = self._selected_definition()
        if definition is None or is_headless():
            return
        areas = (list(self.project_tree.areas())
                 if hasattr(self.project_tree, "areas") else [])
        names = [str(document.get("name") or "")
                 for _path, document in self._module_documents()]
        dialog = ModuleInstanceCreationDialog(
            definition, areas, existing_names=names, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.create_instance(
                dialog.module_name(), overrides=dialog.overrides(),
                area_id=dialog.area_id(), unit_name=dialog.unit_name())
        except Exception as exc:
            self._error("Create Linked Instance", exc)

    def _overrides_clicked(self):
        definition = self._selected_definition()
        document = self._active_document()
        if definition is None or document is None or is_headless():
            return
        dialog = ModuleInstanceOverridesDialog(
            definition, document=document, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.set_active_overrides(dialog.overrides())
        except Exception as exc:
            self._error("Instance Properties", exc)

    def _refresh_clicked(self):
        if is_headless():
            return
        try:
            review = ModuleClassUpdateReviewDialog(
                self.update_plan(), parent=self)
            if review.exec() != QDialog.Accepted:
                return
            self.refresh_active(
                preserve_deviations=(
                    review.choice == ModuleClassUpdateReviewDialog.PRESERVE))
        except Exception as exc:
            self._error("Adopt Module Class", exc)

    def _unlink_clicked(self):
        if is_headless():
            return
        accepted = QMessageBox.question(
            self, "Unlink Module Class",
            "Keep the active module's effective logic but remove its class "
            "identity and future update relationship?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if accepted != QMessageBox.Yes:
            return
        try:
            self.unlink_active()
        except Exception as exc:
            self._error("Unlink Module Class", exc)

    def _delete_clicked(self):
        definition = self._selected_definition()
        if definition is None or self._instances_for(definition.id):
            return
        if not is_headless():
            accepted = QMessageBox.question(
                self, "Delete Module Class",
                f"Archive and remove unused class {definition.name}?",
                QMessageBox.Yes | QMessageBox.No)
            if accepted != QMessageBox.Yes:
                return
        try:
            self.library.delete(definition.id)
            self.reload()
        except Exception as exc:
            self._error("Delete Module Class", exc)

    def _error(self, title: str, error: Exception):
        if not is_headless():
            QMessageBox.critical(self, title, str(error))
