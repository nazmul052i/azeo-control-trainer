"""Equipment Module Properties dialog — Azeo / Honeywell Experion style.

Create or edit an Equipment Module: name, type, child CMs/SFCs,
parameters, alarms, and equipment states.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from pathlib import Path

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from azeo_control_trainer.core.strategy.serialization.equipment_io import EQUIPMENT_TYPES
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    STRATEGY_DIR, list_strategy_folders,
)

# ── Styles ────────────────────────────────────────────────────────────

_DLG_STYLE = f"""
QDialog {{
    background: {UI.pane};
    font-family: Segoe UI, sans-serif;
}}
QLabel {{
    color: {UI.blue};
    font-size: 9pt;
}}
QLineEdit, QTextEdit, QComboBox {{
    background: #FFFFFF;
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 9pt;
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {UI.blue};
}}
QTabWidget::pane {{
    border: 1px solid {UI.border};
    background: #F7F8FA;
    border-radius: 3px;
}}
QTabBar::tab {{
    background: #E8EAF0;
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-bottom: none;
    padding: 6px 14px;
    font-size: 9pt;
    min-width: 80px;
}}
QTabBar::tab:selected {{
    background: #F7F8FA;
    font-weight: bold;
    border-bottom: 2px solid {UI.blue};
}}
QTabBar::tab:hover {{
    background: {UI.hover};
}}
QGroupBox {{
    border: 1px solid {UI.border};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 16px;
    font-size: 9pt;
    font-weight: bold;
    color: {UI.blue};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QPushButton {{
    background: {UI.chrome};
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-radius: 3px;
    padding: 5px 14px;
    font-size: 9pt;
    font-weight: bold;
}}
QPushButton:hover {{
    background: {UI.selection};
}}
QPushButton:disabled {{
    color: {UI.disabled};
}}
QTableWidget {{
    background: #FFFFFF;
    border: 1px solid {UI.border};
    font-size: 9pt;
    gridline-color: #E0E4EA;
}}
QTableWidget::item {{
    padding: 3px 6px;
}}
QTableWidget::item:selected {{
    background: {UI.selection};
    color: {UI.blue};
}}
QHeaderView::section {{
    background: #E8EAF0;
    color: {UI.blue};
    border: 1px solid {UI.border};
    padding: 4px 6px;
    font-size: 9pt;
    font-weight: bold;
}}
QListWidget {{
    background: #FFFFFF;
    border: 1px solid {UI.border};
    font-size: 9pt;
}}
QListWidget::item {{
    padding: 3px 6px;
}}
QListWidget::item:selected {{
    background: {UI.selection};
    color: {UI.blue};
}}
"""


class EquipmentModuleDialog(QDialog):
    """Create or edit an Equipment Module."""

    def __init__(self, existing_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._data = existing_data or {}
        self._is_new = existing_data is None

        title = "New Equipment Module" if self._is_new else "Equipment Module Properties"
        self.setWindowTitle(title)
        self.setMinimumSize(680, 560)
        self.resize(740, 620)
        self.setStyleSheet(_DLG_STYLE)

        self._build_ui()
        if not self._is_new:
            self._populate_from_data()

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        hdr = QLabel("New Equipment Module" if self._is_new
                      else "Equipment Module Properties")
        hdr.setFont(QFont("Segoe UI", 11, QFont.Bold))
        hdr.setStyleSheet(f"color: {UI.blue}; padding-bottom: 4px;")
        layout.addWidget(hdr)

        # Top form: name, type, tag prefix, description
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        form.addWidget(QLabel("Name:"), 0, 0)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Reactor Control")
        form.addWidget(self._name_edit, 0, 1)

        form.addWidget(QLabel("Type:"), 0, 2)
        self._type_combo = AuthoringComboBox()
        self._type_combo.addItems(EQUIPMENT_TYPES)
        self._type_combo.setEditable(True)
        form.addWidget(self._type_combo, 0, 3)

        form.addWidget(QLabel("Tag Prefix:"), 1, 0)
        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText("e.g. REACTOR, F01")
        form.addWidget(self._prefix_edit, 1, 1)

        form.addWidget(QLabel("Description:"), 1, 2)
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Brief description")
        form.addWidget(self._desc_edit, 1, 3)

        layout.addLayout(form)

        # Tabs
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        self._tabs.addTab(self._build_modules_tab(), "Modules")
        self._tabs.addTab(self._build_parameters_tab(), "Parameters")
        self._tabs.addTab(self._build_alarms_tab(), "Alarms")
        self._tabs.addTab(self._build_states_tab(), "States")

        # Button box
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    # ── Modules Tab ───────────────────────────────────────────────────

    def _build_modules_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Available modules (left)
        avail_box = QGroupBox("Available Modules")
        avail_lay = QVBoxLayout(avail_box)
        self._avail_list = QListWidget()
        self._avail_list.setSelectionMode(QListWidget.ExtendedSelection)
        avail_lay.addWidget(self._avail_list)
        layout.addWidget(avail_box, 1)

        # Add/Remove buttons (center)
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.addStretch()

        btn_add_cm = QPushButton("Add CM >>")
        btn_add_cm.setToolTip("Add selected as Control Module")
        btn_add_cm.clicked.connect(lambda: self._add_selected("cm"))
        btn_col.addWidget(btn_add_cm)

        btn_add_sfc = QPushButton("Add SFC >>")
        btn_add_sfc.setToolTip("Add selected as SFC Module")
        btn_add_sfc.clicked.connect(lambda: self._add_selected("sfc"))
        btn_col.addWidget(btn_add_sfc)

        btn_remove = QPushButton("<< Remove")
        btn_remove.clicked.connect(self._remove_selected_assigned)
        btn_col.addWidget(btn_remove)

        btn_col.addStretch()
        layout.addLayout(btn_col)

        # Assigned modules (right)
        assigned_box = QGroupBox("Assigned Modules")
        assigned_lay = QVBoxLayout(assigned_box)

        lbl_cm = QLabel("Control Modules:")
        lbl_cm.setStyleSheet("font-weight: bold; font-size: 9pt;")
        assigned_lay.addWidget(lbl_cm)
        self._cm_list = QListWidget()
        self._cm_list.setSelectionMode(QListWidget.ExtendedSelection)
        assigned_lay.addWidget(self._cm_list)

        lbl_sfc = QLabel("SFC Modules:")
        lbl_sfc.setStyleSheet("font-weight: bold; font-size: 9pt;")
        assigned_lay.addWidget(lbl_sfc)
        self._sfc_list = QListWidget()
        self._sfc_list.setSelectionMode(QListWidget.ExtendedSelection)
        assigned_lay.addWidget(self._sfc_list)

        layout.addWidget(assigned_box, 1)

        # Populate available list
        self._populate_available_modules()

        return w

    def _populate_available_modules(self):
        """Fill the available modules list with all strategy files."""
        self._avail_list.clear()
        folders = list_strategy_folders()

        for folder_name, paths in sorted(folders.items()):
            for p in paths:
                rel = p.relative_to(STRATEGY_DIR)
                display = p.stem.replace("_", " ")
                if folder_name:
                    display = f"[{folder_name}] {display}"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, str(rel))
                item.setToolTip(str(p))
                self._avail_list.addItem(item)

    def _add_selected(self, target: str):
        """Add selected available modules to CM or SFC list."""
        dest = self._cm_list if target == "cm" else self._sfc_list
        for item in self._avail_list.selectedItems():
            rel_path = item.data(Qt.UserRole)
            # Check not already assigned
            existing = [dest.item(i).data(Qt.UserRole)
                        for i in range(dest.count())]
            if rel_path not in existing:
                new_item = QListWidgetItem(item.text())
                new_item.setData(Qt.UserRole, rel_path)
                dest.addItem(new_item)

    def _remove_selected_assigned(self):
        """Remove selected items from assigned lists."""
        for lst in (self._cm_list, self._sfc_list):
            for item in lst.selectedItems():
                lst.takeItem(lst.row(item))

    # ── Parameters Tab ────────────────────────────────────────────────

    def _build_parameters_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        self._param_table = QTableWidget(0, 4)
        self._param_table.setHorizontalHeaderLabels(
            ["Name", "Value", "Unit", "Description"])
        self._param_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self._param_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self._param_table.setAlternatingRowColors(True)
        layout.addWidget(self._param_table, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add Parameter")
        btn_add.clicked.connect(self._add_parameter_row)
        btn_row.addWidget(btn_add)
        btn_del = QPushButton("- Remove Selected")
        btn_del.clicked.connect(self._remove_parameter_row)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return w

    def _add_parameter_row(self, name="", value="", unit="", desc=""):
        row = self._param_table.rowCount()
        self._param_table.insertRow(row)
        self._param_table.setItem(row, 0, QTableWidgetItem(str(name)))
        self._param_table.setItem(row, 1, QTableWidgetItem(str(value)))
        self._param_table.setItem(row, 2, QTableWidgetItem(str(unit)))
        self._param_table.setItem(row, 3, QTableWidgetItem(str(desc)))

    def _remove_parameter_row(self):
        rows = sorted(set(idx.row() for idx in self._param_table.selectedIndexes()),
                       reverse=True)
        for r in rows:
            self._param_table.removeRow(r)

    # ── Alarms Tab ────────────────────────────────────────────────────

    def _build_alarms_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        self._alarm_table = QTableWidget(0, 4)
        self._alarm_table.setHorizontalHeaderLabels(
            ["Alarm Name", "Tag", "Limit", "Priority"])
        self._alarm_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._alarm_table.setAlternatingRowColors(True)
        layout.addWidget(self._alarm_table, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add Alarm")
        btn_add.clicked.connect(self._add_alarm_row)
        btn_row.addWidget(btn_add)
        btn_del = QPushButton("- Remove Selected")
        btn_del.clicked.connect(self._remove_alarm_row)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return w

    def _add_alarm_row(self, name="", tag="", limit="", priority="HIGH"):
        row = self._alarm_table.rowCount()
        self._alarm_table.insertRow(row)
        self._alarm_table.setItem(row, 0, QTableWidgetItem(str(name)))
        self._alarm_table.setItem(row, 1, QTableWidgetItem(str(tag)))
        self._alarm_table.setItem(row, 2, QTableWidgetItem(str(limit)))

        combo = AuthoringComboBox()
        combo.addItems(["LOW", "MEDIUM", "HIGH", "CRITICAL"])
        combo.setCurrentText(str(priority))
        self._alarm_table.setCellWidget(row, 3, combo)

    def _remove_alarm_row(self):
        rows = sorted(set(idx.row() for idx in self._alarm_table.selectedIndexes()),
                       reverse=True)
        for r in rows:
            self._alarm_table.removeRow(r)

    # ── States Tab ────────────────────────────────────────────────────

    def _build_states_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # State list
        left = QVBoxLayout()
        left.addWidget(QLabel("Equipment States:"))
        self._state_list = QListWidget()
        left.addWidget(self._state_list, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add")
        btn_add.clicked.connect(self._add_state)
        btn_row.addWidget(btn_add)
        btn_del = QPushButton("- Remove")
        btn_del.clicked.connect(self._remove_state)
        btn_row.addWidget(btn_del)
        btn_up = QPushButton("Up")
        btn_up.clicked.connect(self._move_state_up)
        btn_row.addWidget(btn_up)
        btn_down = QPushButton("Down")
        btn_down.clicked.connect(self._move_state_down)
        btn_row.addWidget(btn_down)
        left.addLayout(btn_row)

        layout.addLayout(left, 1)

        # Initial state
        right = QVBoxLayout()
        right.addWidget(QLabel("Initial State:"))
        self._initial_state_combo = AuthoringComboBox()
        right.addWidget(self._initial_state_combo)

        right.addSpacing(16)
        note = QLabel(
            "States define the equipment operating phases.\n"
            "Common states: IDLE, STARTING, RUNNING,\n"
            "HOLDING, STOPPING, FAULTED.\n\n"
            "The initial state is set when the\n"
            "equipment module is first loaded.")
        note.setStyleSheet(
            f"color: {UI.border}; font-size: 9pt; font-style: italic;")
        note.setWordWrap(True)
        right.addWidget(note)
        right.addStretch()
        layout.addLayout(right)

        # Default states
        if self._is_new:
            for s in ["IDLE", "STARTING", "RUNNING",
                       "HOLDING", "STOPPING", "FAULTED"]:
                self._state_list.addItem(s)
            self._sync_initial_state_combo()

        return w

    def _add_state(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add State", "State name:")
        if ok and name.strip():
            self._state_list.addItem(name.strip().upper())
            self._sync_initial_state_combo()

    def _remove_state(self):
        for item in self._state_list.selectedItems():
            self._state_list.takeItem(self._state_list.row(item))
        self._sync_initial_state_combo()

    def _move_state_up(self):
        row = self._state_list.currentRow()
        if row > 0:
            item = self._state_list.takeItem(row)
            self._state_list.insertItem(row - 1, item)
            self._state_list.setCurrentRow(row - 1)

    def _move_state_down(self):
        row = self._state_list.currentRow()
        if row < self._state_list.count() - 1:
            item = self._state_list.takeItem(row)
            self._state_list.insertItem(row + 1, item)
            self._state_list.setCurrentRow(row + 1)

    def _sync_initial_state_combo(self):
        current = self._initial_state_combo.currentText()
        self._initial_state_combo.clear()
        states = [self._state_list.item(i).text()
                  for i in range(self._state_list.count())]
        self._initial_state_combo.addItems(states)
        if current in states:
            self._initial_state_combo.setCurrentText(current)
        elif states:
            self._initial_state_combo.setCurrentIndex(0)

    # ── Populate from existing data ───────────────────────────────────

    def _populate_from_data(self):
        d = self._data
        self._name_edit.setText(d.get("name", ""))
        self._desc_edit.setText(d.get("description", ""))
        self._prefix_edit.setText(d.get("tag_prefix", ""))

        eq_type = d.get("equipment_type", "CUSTOM")
        idx = self._type_combo.findText(eq_type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        else:
            self._type_combo.setEditText(eq_type)

        # Assigned modules
        for cm in d.get("control_modules", []):
            display = Path(cm).stem.replace("_", " ")
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, cm)
            self._cm_list.addItem(item)

        for sfc in d.get("sfc_modules", []):
            display = Path(sfc).stem.replace("_", " ")
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, sfc)
            self._sfc_list.addItem(item)

        # Parameters
        for name, info in d.get("parameters", {}).items():
            if isinstance(info, dict):
                self._add_parameter_row(
                    name, info.get("value", ""),
                    info.get("unit", ""), info.get("description", ""))
            else:
                self._add_parameter_row(name, info)

        # Alarms
        for name, info in d.get("alarms", {}).items():
            if isinstance(info, dict):
                self._add_alarm_row(
                    name, info.get("tag", ""),
                    info.get("limit", ""), info.get("priority", "HIGH"))

        # States
        self._state_list.clear()
        for s in d.get("states", []):
            self._state_list.addItem(s)
        self._sync_initial_state_combo()
        init = d.get("initial_state", "")
        if init:
            self._initial_state_combo.setCurrentText(init)

    # ── Get data ──────────────────────────────────────────────────────

    def get_data(self) -> dict:
        """Return the equipment module dict from current dialog state."""
        # Parameters
        params = {}
        for r in range(self._param_table.rowCount()):
            name = (self._param_table.item(r, 0).text().strip()
                    if self._param_table.item(r, 0) else "")
            if not name:
                continue
            val = (self._param_table.item(r, 1).text().strip()
                   if self._param_table.item(r, 1) else "")
            unit = (self._param_table.item(r, 2).text().strip()
                    if self._param_table.item(r, 2) else "")
            desc = (self._param_table.item(r, 3).text().strip()
                    if self._param_table.item(r, 3) else "")
            # Try numeric conversion
            try:
                val = float(val)
                if val == int(val):
                    val = int(val)
            except (ValueError, TypeError):
                pass
            params[name] = {"value": val, "unit": unit, "description": desc}

        # Alarms
        alarms = {}
        for r in range(self._alarm_table.rowCount()):
            name = (self._alarm_table.item(r, 0).text().strip()
                    if self._alarm_table.item(r, 0) else "")
            if not name:
                continue
            tag = (self._alarm_table.item(r, 1).text().strip()
                   if self._alarm_table.item(r, 1) else "")
            limit_str = (self._alarm_table.item(r, 2).text().strip()
                         if self._alarm_table.item(r, 2) else "")
            try:
                limit_val = float(limit_str)
            except (ValueError, TypeError):
                limit_val = limit_str
            combo = self._alarm_table.cellWidget(r, 3)
            priority = combo.currentText() if combo else "HIGH"
            alarms[name] = {"tag": tag, "limit": limit_val, "priority": priority}

        # States
        states = [self._state_list.item(i).text()
                  for i in range(self._state_list.count())]

        # Control / SFC modules
        cms = [self._cm_list.item(i).data(Qt.UserRole)
               for i in range(self._cm_list.count())]
        sfcs = [self._sfc_list.item(i).data(Qt.UserRole)
                for i in range(self._sfc_list.count())]

        return {
            "name": self._name_edit.text().strip(),
            "description": self._desc_edit.text().strip(),
            "equipment_type": self._type_combo.currentText().strip(),
            "tag_prefix": self._prefix_edit.text().strip(),
            "parameters": params,
            "control_modules": cms,
            "sfc_modules": sfcs,
            "alarms": alarms,
            "states": states,
            "initial_state": self._initial_state_combo.currentText(),
        }

    def _on_accept(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Equipment module name is required.")
            self._name_edit.setFocus()
            return
        eq_type = self._type_combo.currentText().strip()
        if not eq_type:
            QMessageBox.warning(self, "Validation", "Equipment type is required.")
            self._type_combo.setFocus()
            return
        self.accept()
