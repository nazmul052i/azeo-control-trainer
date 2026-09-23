"""Visual last-download diff and dependency impact for Control Designer."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QHeaderView,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    ENGINEERING_QSS, polish_dialog,
)
from azeo_control_trainer.core.strategy.deployment_analysis import (
    LAYOUT, analyze_deployment,
)


_SCOPE_COLORS = {
    "structure": QColor("#B71C1C"),
    "parameter": QColor("#A65A00"),
    "runtime": QColor("#6A3D9A"),
    "layout": QColor(UI.text_secondary),
    "documentation": QColor(UI.text_secondary),
}


def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


class DeploymentImpactDialog(QDialog):
    """Compare every open module against its successful-download snapshot."""

    navigateRequested = Signal(str, str)  # module name, block id

    def __init__(self, canvases, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deployment Diff and Impact")
        self.resize(1080, 680)
        self.setMinimumSize(820, 520)
        self.setStyleSheet(ENGINEERING_QSS)
        self._canvases = list(canvases)
        documents = [canvas.scene.graph for canvas in self._canvases]
        self.reports = [analyze_deployment(
            getattr(canvas, "download_snapshot", None),
            canvas.scene.graph,
            project_documents=documents,
        ) for canvas in self._canvases]
        self._show_layout = True
        self._build_ui()
        polish_dialog(self)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel("Deployment Diff and Dependency Impact")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            f"color: {UI.text_secondary}; padding-bottom: 4px;")
        layout.addWidget(self._summary)

        splitter = QSplitter(Qt.Horizontal)
        self._modules = QListWidget()
        self._modules.setMinimumWidth(230)
        self._modules.setMaximumWidth(330)
        for index, report in enumerate(self.reports):
            baseline = "download baseline" if report.baseline_available else "new download"
            item = QListWidgetItem(
                f"{report.module}\n{report.risk} · {baseline}")
            item.setData(Qt.UserRole, index)
            if report.risk == "HIGH":
                item.setForeground(QColor("#B71C1C"))
            elif report.risk == "MEDIUM":
                item.setForeground(QColor("#A65A00"))
            self._modules.addItem(item)
        self._modules.currentRowChanged.connect(self._show_report)
        splitter.addWidget(self._modules)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self._module_summary = QLabel()
        self._module_summary.setStyleSheet(
            f"font-weight: bold; color: {UI.blue};")
        controls.addWidget(self._module_summary, 1)
        self._layout_toggle = QCheckBox("Include drawing-only changes")
        self._layout_toggle.setChecked(True)
        self._layout_toggle.toggled.connect(self._toggle_layout)
        controls.addWidget(self._layout_toggle)
        right_layout.addLayout(controls)

        tabs = QTabWidget()
        self._changes = QTableWidget(0, 6)
        self._changes.setHorizontalHeaderLabels([
            "Scope", "Action", "Object", "Difference", "Before", "After"])
        self._configure_table(self._changes)
        self._changes.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        tabs.addTab(self._changes, "Differences")

        self._impacts = QTableWidget(0, 5)
        self._impacts.setHorizontalHeaderLabels([
            "Module", "Affected object", "Distance", "Reason", "External"])
        self._configure_table(self._impacts)
        self._impacts.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self._impacts.cellDoubleClicked.connect(self._navigate)
        tabs.addTab(self._impacts, "Dependency Impact")
        right_layout.addWidget(tabs, 1)
        splitter.addWidget(right)
        splitter.setSizes([250, 800])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        overall_changes = sum(len(report.controller_changes)
                              for report in self.reports)
        high = sum(report.restart_required for report in self.reports)
        self._summary.setText(
            f"{len(self.reports)} open module(s) · {overall_changes} controller "
            f"change(s) · {high} module(s) require re-download. "
            "Double-click an affected block to navigate to it.")
        if self.reports:
            self._modules.setCurrentRow(0)
        else:
            self._summary.setText("No control modules are open.")

    @staticmethod
    def _configure_table(table: QTableWidget):
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def _toggle_layout(self, checked: bool):
        self._show_layout = bool(checked)
        self._show_report(self._modules.currentRow())

    def _show_report(self, row: int):
        if row < 0 or row >= len(self.reports):
            return
        report = self.reports[row]
        self._module_summary.setText(
            f"{report.module} · Risk {report.risk} · {report.summary()}")
        changes = [change for change in report.changes
                   if self._show_layout or change.scope != LAYOUT]
        self._changes.setRowCount(len(changes))
        for r, change in enumerate(changes):
            values = (
                change.scope.title(), change.action.title(), change.label,
                change.detail, _show(change.before), _show(change.after))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setForeground(_SCOPE_COLORS.get(
                        change.scope, QColor(UI.text)))
                self._changes.setItem(r, column, item)

        self._impacts.setRowCount(len(report.impacts))
        for r, impact in enumerate(report.impacts):
            values = (
                impact.module, impact.label, str(impact.distance),
                impact.reason, "Yes" if impact.external else "No")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, impact.object_id)
                if impact.external:
                    item.setForeground(QColor("#6A3D9A"))
                self._impacts.setItem(r, column, item)

    def _navigate(self, row: int, _column: int):
        module = self._impacts.item(row, 0)
        block = self._impacts.item(row, 1)
        if module is not None and block is not None:
            self.navigateRequested.emit(
                module.text(), str(block.data(Qt.UserRole) or ""))
