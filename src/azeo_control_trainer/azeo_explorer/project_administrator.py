"""Commercial project lifecycle UI over :mod:`project_admin` services."""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .project_admin import ProjectAdministrationError, ProjectAdministrator
from azeo_control_trainer.core.presentation.headless import is_headless


_QSS = f"""
QDialog {{ background: {UI.chrome}; color: {UI.text}; }}
QWidget#projectHeader {{ background: #FFFFFF; border-bottom: 1px solid {UI.border}; }}
QLabel#projectTitle {{ color: {UI.blue}; font-size: 12.75pt; font-weight: 700; }}
QLabel#projectSubtitle {{ color: {UI.text_secondary}; }}
QTableWidget {{ background: #FFFFFF; border: 1px solid {UI.border}; }}
QTableWidget::item {{ min-height: 26px; }}
QHeaderView::section {{ background: {UI.chrome}; color: {UI.text_secondary}; padding: 5px;
    border: none; border-right: 1px solid {UI.border}; font-weight: 600; }}
QPushButton {{ min-height: 27px; padding: 2px 10px; }}
QPushButton#primary {{ background: {UI.blue}; color: #FFFFFF; border: none;
    border-radius: 3px; font-weight: 600; }}
"""


class _NewProjectDialog(QDialog):
    def __init__(self, projects: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Engineering Project")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("MyEngineeringProject")
        self.description = QLineEdit()
        self.preserve = QCheckBox(
            "Preserve the selected project's controller and I/O network")
        self.source = QLineEdit(projects[0] if projects else "")
        self.source.setEnabled(False)
        self.preserve.toggled.connect(self.source.setEnabled)
        form.addRow("Project name", self.name)
        form.addRow("Description", self.description)
        form.addRow(self.preserve)
        form.addRow("Network source", self.source)
        note = QLabel(
            "A new project contains no control modules or displays. Network "
            "preservation copies only controller, I/O, and console topology.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "description": self.description.text().strip(),
            "preserve_network_from": (
                self.source.text().strip() if self.preserve.isChecked()
                else None),
        }


class ProjectAdministratorDialog(QDialog):
    """Create, protect, select, migrate, and recover engineering projects."""

    def __init__(self, manager: ProjectAdministrator, current_project: Path,
                 parent=None):
        super().__init__(parent)
        self.manager = manager
        self.current_project = Path(current_project).resolve()
        self.records = []
        self._configuration_dialog = None
        self.setWindowTitle("Azeo Project Administrator")
        self.resize(1080, 650)
        self.setStyleSheet(_QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(objectName="projectHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 10)
        header_layout.setSpacing(2)
        header_layout.addWidget(QLabel(
            "AZEO PROJECT ADMINISTRATOR", objectName="projectTitle"))
        header_layout.addWidget(QLabel(
            "Manage the complete engineering configuration. Backups include "
            "control, I/O, displays, PVM revisions, and workstation data.",
            objectName="projectSubtitle"))
        root.addWidget(header)

        actions = QHBoxLayout()
        actions.setContentsMargins(12, 8, 12, 8)
        definitions = (
            ("New…", self.create_project, True),
            ("Copy…", self.copy_project, False),
            ("Rename…", self.rename_project, False),
            ("Set Active", self.set_active, False),
            ("Backup…", self.backup_project, False),
            ("Restore…", self.restore_project, False),
            ("Register…", self.register_project, False),
            ("Migrate", self.migrate_project, False),
            ("Deregister", self.deregister_project, False),
        )
        for label, handler, primary in definitions:
            button = QPushButton(label)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(
                lambda _checked=False, callback=handler: callback())
            actions.addWidget(button)
        actions.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        root.addLayout(actions)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Active", "Project", "Schema", "Modules", "Displays",
            "Modified", "Status", "Location",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(lambda _item: self.set_active())
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 10)
        self.summary = QLabel()
        footer.addWidget(self.summary, 1)
        database = QPushButton("Configuration Database…")
        database.clicked.connect(self.open_configuration_database)
        footer.addWidget(database)
        recovery = QPushButton("Database recovery…")
        recovery.clicked.connect(self.open_database_recovery)
        footer.addWidget(recovery)
        verify = QPushButton("Verify Selected")
        verify.clicked.connect(self.verify_project)
        footer.addWidget(verify)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)
        self.refresh()

    def open_configuration_database(self):
        from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog
        selected = self._selected()
        project = selected.path if selected else self.current_project
        if self._configuration_dialog is None:
            self._configuration_dialog = ConfigurationCatalogDialog(
                project, self, initial_page="capture")
        elif self._configuration_dialog.source_project != project:
            self._configuration_dialog.set_source_project(project)
        self._configuration_dialog.show()
        self._configuration_dialog.raise_()
        self._configuration_dialog.activateWindow()
        return self._configuration_dialog

    def open_database_recovery(self):
        dialog = self.open_configuration_database()
        if dialog.workspace.open_page("recovery") is None:
            dialog.workspace._initial = "recovery"
        return dialog

    def refresh(self) -> None:
        self.records = self.manager.inventory()
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            values = (
                "● Active" if record.active else "",
                record.name,
                record.schema_version or "Legacy",
                record.modules,
                record.displays,
                datetime.fromtimestamp(record.modified).strftime(
                    "%Y-%m-%d %H:%M"),
                record.status,
                str(record.path),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, record)
                if record.path.resolve() == self.current_project:
                    font = QFont(self.table.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, column, item)
        self.summary.setText(
            f"{len(self.records)} registered project(s) · active selection "
            "takes effect on the next application launch")

    def _selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def _run(self, title: str, function, *args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except (ProjectAdministrationError, OSError, ValueError) as error:
            if not is_headless():
                QMessageBox.critical(self, title, str(error))
            return None
        self._audit(title.lower().replace(" ", "_"), str(result))
        self.refresh()
        return result

    def create_project(self, values: dict[str, Any] | None = None):
        if values is None:
            if is_headless():
                return None
            dialog = _NewProjectDialog([row.name for row in self.records], self)
            if dialog.exec() != QDialog.Accepted:
                return None
            values = dialog.values()
        return self._run("Create Project", self.manager.create, **values)

    def copy_project(self, target_name: str | None = None):
        record = self._selected()
        if record is None:
            return None
        if target_name is None:
            if is_headless():
                return None
            from PySide6.QtWidgets import QInputDialog
            target_name, ok = QInputDialog.getText(
                self, "Copy Project", "New project name:",
                text=f"{record.name}_Copy")
            if not ok:
                return None
        return self._run(
            "Copy Project", self.manager.copy, record.name, target_name)

    def rename_project(self, new_name: str | None = None):
        record = self._selected()
        if record is None:
            return None
        if record.path.resolve() == self.current_project:
            return self._blocked(
                "Rename Project",
                "Close the currently open project before renaming it.")
        if new_name is None:
            if is_headless():
                return None
            from PySide6.QtWidgets import QInputDialog
            new_name, ok = QInputDialog.getText(
                self, "Rename Project", "New project name:", text=record.name)
            if not ok:
                return None
        return self._run(
            "Rename Project", self.manager.rename, record.name, new_name)

    def set_active(self):
        record = self._selected()
        if record is None:
            return None
        result = self._run("Set Active Project", self.manager.set_active,
                           record.name)
        if result is not None and not is_headless():
            QMessageBox.information(
                self, "Active Project",
                f"{record.name} will open by default on the next launch. "
                "The current engineering session was not switched underneath you.")
        return result

    def backup_project(self, destination: Path | str | None = None):
        record = self._selected()
        if record is None:
            return None
        if destination is None and not is_headless():
            selected, _ = QFileDialog.getSaveFileName(
                self, "Back Up Engineering Project",
                str(self.manager.backup_root / record.name /
                    f"{record.name}.azeoproject"),
                "Azeo Engineering Project (*.azeoproject)")
            if not selected:
                return None
            destination = selected
        return self._run(
            "Backup Project", self.manager.backup, record.name, destination)

    def restore_project(self, archive: Path | str | None = None,
                        project_name: str | None = None):
        if archive is None:
            if is_headless():
                return None
            selected, _ = QFileDialog.getOpenFileName(
                self, "Restore Engineering Project",
                str(self.manager.backup_root),
                "Azeo Engineering Project (*.azeoproject)")
            if not selected:
                return None
            archive = selected
            from PySide6.QtWidgets import QInputDialog
            suggested = Path(selected).stem.split("-")[0]
            project_name, ok = QInputDialog.getText(
                self, "Restore Engineering Project",
                "Registered project name:", text=suggested)
            if not ok or not project_name:
                return None
        return self._run(
            "Restore Project", self.manager.restore, archive, project_name)

    def register_project(self, source: Path | str | None = None,
                         name: str | None = None):
        if source is None:
            if is_headless():
                return None
            source = QFileDialog.getExistingDirectory(
                self, "Register Engineering Project")
            if not source:
                return None
        return self._run(
            "Register Project", self.manager.register_directory, source, name)

    def migrate_project(self):
        record = self._selected()
        if record is None:
            return None
        changes = self.manager.migration_preview(record.name)
        if not changes:
            if not is_headless():
                QMessageBox.information(
                    self, "Migrate Project", "The project schema is current.")
            return []
        if not is_headless() and QMessageBox.question(
                self, "Migrate Project",
                "Apply these metadata-only changes?\n\n• "
                + "\n• ".join(changes),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return None
        return self._run("Migrate Project", self.manager.migrate, record.name)

    def deregister_project(self):
        record = self._selected()
        if record is None:
            return None
        if record.path.resolve() == self.current_project:
            return self._blocked(
                "Deregister Project",
                "Close the currently open project before deregistering it.")
        if not is_headless() and QMessageBox.question(
                self, "Deregister Project",
                f"Move {record.name} to the recoverable _deregistered area?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return None
        return self._run(
            "Deregister Project", self.manager.deregister, record.name)

    def verify_project(self):
        record = self._selected()
        if record is None:
            return None
        issues = self.manager.verify(record.name)
        if not is_headless():
            QMessageBox.information(
                self, "Project Verification",
                "No project-integrity issues found." if not issues else
                "Issues found:\n\n• " + "\n• ".join(issues))
        return issues

    def _blocked(self, title: str, text: str):
        if not is_headless():
            QMessageBox.information(self, title, text)
        return None

    @staticmethod
    def _audit(action: str, target: str) -> None:
        from ..config.logging_config import audit_event
        audit_event(
            "explorer", f"project_administrator.{action}",
            target=target, outcome="success")


__all__ = ["ProjectAdministratorDialog"]
