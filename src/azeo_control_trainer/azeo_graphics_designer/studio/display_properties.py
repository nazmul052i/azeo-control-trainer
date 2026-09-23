"""Display-document properties shared by every Graphics Designer canvas.

The Explorer used to make an engineer open a display, clear the selection,
and discover the same fields in the inspector.  This dialog is the explicit
document-level route: it edits the existing :class:`PvmDisplay` model and
therefore applies equally to L1, L2, L3 and L4 displays and to displays made
from any template.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout,
)
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)

if TYPE_CHECKING:                       # pragma: no cover
    from .assembler import PvmStudio


CANVAS_PRESETS = (
    ("Auto · content bounds", (0, 0)),
    ("16:9 operator · 1600 × 900", (1600, 900)),
    ("Full HD · 1920 × 1080", (1920, 1080)),
    ("4:3 engineering · 1600 × 1200", (1600, 1200)),
)


class DisplayPropertiesDialog(QDialog):
    """Edit every scalar canvas property on one ``PvmDisplay`` draft."""

    def __init__(self, studio: "PvmStudio", parent=None):
        super().__init__(parent or studio)
        self.studio = studio
        self.setWindowTitle(f"Display Properties — {studio.display.name}")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            root,
            "Display properties",
            "Canvas, hierarchy, appearance and engineering status for "
            f"{studio.display.name}.",
        )
        identity = QGroupBox("Display")
        identity_form = QFormLayout(identity)
        name = QLabel(studio.display.name)
        name.setTextInteractionFlags(name.textInteractionFlags()
                                     | Qt.TextSelectableByMouse)
        identity_form.addRow("Name", name)
        self.description = QPlainTextEdit(studio.display.description)
        self.description.setMaximumHeight(72)
        identity_form.addRow("Description", self.description)
        self.level = AuthoringComboBox()
        for value, title in (
                (1, "L1 · Plant overview"),
                (2, "L2 · Unit operation"),
                (3, "L3 · Equipment detail"),
                (4, "L4 · Diagnostics / support")):
            self.level.addItem(title, value)
        self.level.setCurrentIndex(max(0, min(3, studio.display.level - 1)))
        identity_form.addRow("Hierarchy level", self.level)
        self.parent_display = AuthoringComboBox()
        self.parent_display.setEditable(True)
        self.parent_display.addItem("")
        if studio.store.root.exists():
            for path in sorted(studio.store.root.iterdir()):
                if path.is_dir() and (path / "draft.json").exists() \
                        and path.name != studio.display.name:
                    self.parent_display.addItem(path.name)
        self.parent_display.setCurrentText(studio.display.parent)
        identity_form.addRow("Parent display", self.parent_display)
        root.addWidget(identity)

        canvas = QGroupBox("Canvas")
        canvas_form = QFormLayout(canvas)
        self.preset = AuthoringComboBox()
        for title, dimensions in CANVAS_PRESETS:
            self.preset.addItem(title, dimensions)
        self.preset.addItem("Custom", None)
        canvas_form.addRow("Page preset", self.preset)

        size_row = QHBoxLayout()
        # Keep QWidget.width()/height() callable. Shadowing those methods with
        # spin boxes broke the shared screen-fitting helper as soon as this
        # dialog was treated like every other authoring surface.
        self.width_spin = self._dimension_spin(studio.display.width)
        self.height_spin = self._dimension_spin(studio.display.height)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_spin)
        canvas_form.addRow("Width × height", size_row)

        self.background = QLineEdit(studio.display.background)
        self.background.setPlaceholderText(
            "Theme background (empty) or #RRGGBB")
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.background, 1)
        self.pick_colour = QPushButton("Choose…")
        self.pick_colour.clicked.connect(self._choose_colour)
        colour_row.addWidget(self.pick_colour)
        canvas_form.addRow("Background", colour_row)

        self.fit = AuthoringComboBox()
        self.fit.addItem("Fit to display frame", "fit_to_frame")
        self.fit.addItem("Fit to drawing content", "fit_to_content")
        self._select_data(self.fit, studio.display.fit)
        canvas_form.addRow("Studio Fit command", self.fit)

        self.view_type = AuthoringComboBox()
        self.view_type.addItem("Scale to display frame", "scale_to_frame")
        self.view_type.addItem("Actual size", "actual_size")
        self._select_data(self.view_type, studio.display.view_type)
        canvas_form.addRow("Operator view", self.view_type)

        self.show_tag = AuthoringComboBox()
        for title, value in (
                ("Module name", "module"),
                ("Description", "description"),
                ("Friendly name", "friendly"),
                ("Hidden", "none")):
            self.show_tag.addItem(title, value)
        self._select_data(self.show_tag, studio.display.show_tag)
        canvas_form.addRow("Default PVM tag", self.show_tag)
        root.addWidget(canvas)

        guides = QGroupBox("Authoring guides")
        guide_form = QFormLayout(guides)
        self.show_grid = QCheckBox("Show the alignment grid in Edit mode")
        self.show_grid.setChecked(studio.grid_visible)
        guide_form.addRow(self.show_grid)
        self.snap_grid = QCheckBox("Snap moved objects to the grid")
        self.snap_grid.setChecked(studio.snap_enabled)
        guide_form.addRow(self.snap_grid)
        boundary_note = QLabel(
            "A dotted, non-published rectangle marks the configured page. "
            "Auto/content-bound canvases have no fixed page boundary.")
        boundary_note.setWordWrap(True)
        guide_form.addRow(boundary_note)
        root.addWidget(guides)

        status = QGroupBox("Engineering status")
        status_form = QFormLayout(status)
        self.work_in_progress = QCheckBox("Work in progress")
        self.work_in_progress.setChecked(studio.display.work_in_progress)
        status_form.addRow(self.work_in_progress)
        self.wip_reason = QLineEdit(studio.display.wip_reason)
        status_form.addRow("Reason", self.wip_reason)
        root.addWidget(status)

        self.error = QLabel("")
        self.error.setStyleSheet("color: #A51E22;")
        self.error.setWordWrap(True)
        root.addWidget(self.error)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Apply")
        self.buttons.accepted.connect(self._accept_changes)
        self.buttons.rejected.connect(self.reject)
        style_dialog_buttons(self.buttons, QDialogButtonBox.Ok)
        root.addWidget(self.buttons)

        self._preset_guard = False
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.width_spin.valueChanged.connect(self._size_changed)
        self.height_spin.valueChanged.connect(self._size_changed)
        self._sync_preset()

    @staticmethod
    def _dimension_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 16384)
        spin.setSpecialValueText("Auto")
        spin.setSuffix(" px")
        spin.setValue(int(value))
        return spin

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _sync_preset(self) -> None:
        dimensions = (self.width_spin.value(), self.height_spin.value())
        index = next((i for i, (_title, size) in enumerate(CANVAS_PRESETS)
                      if size == dimensions), len(CANVAS_PRESETS))
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(index)

    def _apply_preset(self, _index: int) -> None:
        if self._preset_guard:
            return
        dimensions = self.preset.currentData()
        if dimensions is None:
            return
        self._preset_guard = True
        try:
            self.width_spin.setValue(dimensions[0])
            self.height_spin.setValue(dimensions[1])
        finally:
            self._preset_guard = False

    def _size_changed(self, _value: int) -> None:
        if not self._preset_guard:
            self._sync_preset()

    def _choose_colour(self) -> None:
        initial = QColor(self.background.text().strip())
        colour = QColorDialog.getColor(
            initial if initial.isValid() else QColor("#E3E6EA"), self,
            "Display background")
        if colour.isValid():
            self.background.setText(colour.name().upper())

    def _accept_changes(self) -> None:
        if self.apply():
            self.accept()

    def apply(self) -> bool:
        """Validate and apply to the open draft; Save remains explicit."""
        width, height = self.width_spin.value(), self.height_spin.value()
        if (width == 0) != (height == 0):
            self.error.setText(
                "Width and height must both be Auto or both be explicit.")
            return False
        background = self.background.text().strip()
        if background and not QColor(background).isValid():
            self.error.setText(
                "Background must be empty or a valid Qt colour such as "
                "#E3E6EA.")
            return False

        display = self.studio.display
        values = {
            "description": self.description.toPlainText().strip(),
            "level": int(self.level.currentData()),
            "parent": self.parent_display.currentText().strip(),
            "width": width,
            "height": height,
            "background": background,
            "fit": self.fit.currentData(),
            "view_type": self.view_type.currentData(),
            "show_tag": self.show_tag.currentData(),
            "work_in_progress": self.work_in_progress.isChecked(),
            "wip_reason": self.wip_reason.text().strip(),
        }
        changed = any(getattr(display, key) != value
                      for key, value in values.items())
        guides_changed = (
            self.studio.grid_visible != self.show_grid.isChecked()
            or self.studio.snap_enabled != self.snap_grid.isChecked())
        if changed:
            self.studio.checkpoint()
            for key, value in values.items():
                setattr(display, key, value)
            self.studio.apply_display_frame()
            self.studio.mark_unsaved()
            self.studio.pane.show_pvm(None)
        if guides_changed:
            self.studio._set_grid(self.show_grid.isChecked())
            self.studio.snap_enabled = self.snap_grid.isChecked()
        self.studio.canvas.viewport().update()
        self.error.clear()
        return True
