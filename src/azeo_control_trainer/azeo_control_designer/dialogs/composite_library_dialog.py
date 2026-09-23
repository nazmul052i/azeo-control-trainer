"""Authoring dialog for versioned, linked composite definitions."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from azeo_control_trainer.core.strategy.composites import (
    PublicParameter,
    project_composite_library,
)
from azeo_control_trainer.core.presentation.headless import is_headless


class CompositeLibraryDialog(QDialog):
    """Publish an embedded composite or manage one linked instance.

    The controller never reads the library at runtime.  Linking installs a
    last-known effective graph into the block; this dialog only manages the
    engineering identity, revision and explicit public-property overrides.
    """

    compositeChanged = Signal()

    def __init__(self, block, library=None, parent=None, change_guard=None):
        super().__init__(parent)
        self.block = block
        self.library = library or project_composite_library()
        # A canvas supplies a transactional guard so a definition refresh
        # cannot strand existing outer wires on removed/incompatible ports.
        # Detached dialog tests and library-only callers keep the direct model
        # behaviour by leaving this unset.
        self.change_guard = change_guard
        self.setWindowTitle(f"Composite Definition — {block.instance_name}")
        self.resize(760, 520)
        self._rebuilding = False

        root = QVBoxLayout(self)
        summary = QGroupBox("Definition")
        form = QFormLayout(summary)
        self.name_edit = QLineEdit(block.instance_name)
        self.description_edit = QLineEdit()
        self.definition_combo = AuthoringComboBox()
        self.state_label = QLabel()
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Definition name", self.name_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Library definition", self.definition_combo)
        form.addRow("Instance state", self.state_label)
        root.addWidget(summary)

        self.parameters = QTableWidget(0, 6)
        self.parameters.setHorizontalHeaderLabels([
            "Public name", "Block/config path", "Type", "Default",
            "Instance value", "Override",
        ])
        self.parameters.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.parameters.setAlternatingRowColors(True)
        self.parameters.horizontalHeader().setStretchLastSection(True)
        self.parameters.cellChanged.connect(self._parameter_changed)
        root.addWidget(self.parameters, 1)

        edit_row = QHBoxLayout()
        self.add_parameter_button = QPushButton("Add Public Property")
        self.remove_parameter_button = QPushButton("Remove Property")
        self.add_parameter_button.clicked.connect(self.add_parameter_row)
        self.remove_parameter_button.clicked.connect(
            self.remove_selected_parameter)
        edit_row.addWidget(self.add_parameter_button)
        edit_row.addWidget(self.remove_parameter_button)
        edit_row.addStretch(1)
        root.addLayout(edit_row)

        actions = QHBoxLayout()
        self.publish_button = QPushButton("Publish Current as Definition")
        self.link_button = QPushButton("Link Selected Definition")
        self.refresh_button = QPushButton("Refresh Instance")
        self.unlink_button = QPushButton("Convert to Embedded")
        self.publish_button.clicked.connect(self._publish_clicked)
        self.link_button.clicked.connect(self._link_clicked)
        self.refresh_button.clicked.connect(self._refresh_clicked)
        self.unlink_button.clicked.connect(self._unlink_clicked)
        for button in (self.publish_button, self.link_button,
                       self.refresh_button, self.unlink_button):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.reload()

    def reload(self) -> None:
        self._rebuilding = True
        selected = self.definition_combo.currentData()
        self.definition_combo.clear()
        for definition in self.library.list():
            self.definition_combo.addItem(
                f"{definition.name}  (r{definition.revision})", definition.id)
        wanted = self.block.definition_id or selected
        index = self.definition_combo.findData(wanted)
        if index >= 0:
            self.definition_combo.setCurrentIndex(index)

        linked = self.block.is_linked
        state = self.block.definition_state(self.library)
        identity = (f"{state.upper()} · revision {self.block.definition_revision}"
                    if linked else "EMBEDDED · independently editable")
        self.state_label.setText(identity)
        self.state_label.setStyleSheet(
            "font-weight: 700; color: "
            + ({"current": "#287A3D", "stale": "#B56B00",
                "missing": "#B3261E"}.get(state, "#526477")) + ";")
        self.publish_button.setEnabled(not linked)
        self.refresh_button.setEnabled(linked and state == "stale")
        self.unlink_button.setEnabled(linked)
        self.link_button.setEnabled(self.definition_combo.count() > 0)
        self.add_parameter_button.setEnabled(not linked)
        self.remove_parameter_button.setEnabled(not linked)
        self._load_parameter_rows()
        self._rebuilding = False

    def _load_parameter_rows(self) -> None:
        self.parameters.setRowCount(0)
        snapshot = None
        if self.block.is_linked:
            try:
                snapshot = self.block._snapshot_definition()
            except (RuntimeError, ValueError):
                snapshot = None
        if snapshot is None:
            return
        for parameter in snapshot.public_parameters:
            row = self.parameters.rowCount()
            self.parameters.insertRow(row)
            values = [parameter.name, parameter.path, parameter.data_type,
                      str(parameter.default),
                      str(self.block.public_parameter_value(parameter.name))]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column < 4:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.parameters.setItem(row, column, item)
            override = QTableWidgetItem()
            override.setFlags(override.flags() | Qt.ItemIsUserCheckable)
            override.setCheckState(
                Qt.Checked if parameter.name in
                self.block.public_parameter_overrides else Qt.Unchecked)
            self.parameters.setItem(row, 5, override)

    def add_parameter_row(self) -> None:
        if self.block.is_linked:
            return
        row = self.parameters.rowCount()
        self.parameters.insertRow(row)
        for column, value in enumerate(("Property", "BLOCK/PARAM", "FLOAT",
                                        "0.0", "", "")):
            item = QTableWidgetItem(value)
            if column == 5:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.parameters.setItem(row, column, item)
        self.parameters.setCurrentCell(row, 0)
        self.parameters.editItem(self.parameters.item(row, 0))

    def remove_selected_parameter(self) -> None:
        if self.block.is_linked:
            return
        rows = sorted({item.row() for item in self.parameters.selectedItems()},
                      reverse=True)
        for row in rows:
            self.parameters.removeRow(row)

    def declared_parameters(self) -> list[PublicParameter]:
        declared = []
        for row in range(self.parameters.rowCount()):
            def text(column):
                item = self.parameters.item(row, column)
                return item.text() if item is not None else ""
            declared.append(PublicParameter(
                name=text(0).strip(), path=text(1).strip(),
                data_type=text(2).strip().upper() or "FLOAT",
                default=text(3).strip(),
            ))
        return declared

    def publish_current(self):
        def publish_and_link():
            definition = self.library.create(
                self.name_edit.text(), self.block.inner_graph.to_dict(),
                description=self.description_edit.text(),
                public_parameters=self.declared_parameters(),
            )
            self.block.link_to_definition(definition)
            return definition

        definition = self._apply_change(publish_and_link)
        self.compositeChanged.emit()
        self.reload()
        return definition

    def link_selected(self) -> bool:
        definition_id = self.definition_combo.currentData()
        if not definition_id:
            return False
        self._apply_change(lambda: self.block.link_to_definition(
            self.library.get(str(definition_id))))
        self.compositeChanged.emit()
        self.reload()
        return True

    def refresh_instance(self) -> bool:
        changed = self._apply_change(lambda: self.block.refresh_from_library(
            self.library, expected_revision=self.block.definition_revision))
        if changed:
            self.compositeChanged.emit()
        self.reload()
        return changed

    def unlink_instance(self) -> bool:
        changed = self._apply_change(self.block.unlink_to_embedded)
        if changed:
            self.compositeChanged.emit()
        self.reload()
        return changed

    def _parameter_changed(self, row: int, column: int) -> None:
        if self._rebuilding or not self.block.is_linked or column not in (4, 5):
            return
        name_item = self.parameters.item(row, 0)
        value_item = self.parameters.item(row, 4)
        override_item = self.parameters.item(row, 5)
        if not (name_item and value_item and override_item):
            return
        name = name_item.text()
        try:
            if override_item.checkState() == Qt.Checked:
                def set_override():
                    self.block.set_public_parameter_override(
                        name, value_item.text())
                    return True
                self._apply_change(set_override)
            else:
                self._apply_change(
                    lambda: self.block.clear_public_parameter_override(name))
        except (TypeError, ValueError, KeyError) as exc:
            self._show_error(str(exc))
            self.reload()
            return
        self.compositeChanged.emit()
        self.reload()

    def _apply_change(self, mutation):
        """Run one model mutation through the owning canvas transaction."""
        if self.change_guard is not None:
            return self.change_guard(mutation)
        return mutation()

    def _show_error(self, message: str) -> None:
        if not is_headless():
            QMessageBox.critical(self, "Composite Definition", message)

    def _publish_clicked(self) -> None:
        try:
            self.publish_current()
        except (OSError, TypeError, ValueError) as exc:
            self._show_error(str(exc))

    def _link_clicked(self) -> None:
        try:
            self.link_selected()
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._show_error(str(exc))

    def _refresh_clicked(self) -> None:
        try:
            self.refresh_instance()
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._show_error(str(exc))

    def _unlink_clicked(self) -> None:
        self.unlink_instance()
