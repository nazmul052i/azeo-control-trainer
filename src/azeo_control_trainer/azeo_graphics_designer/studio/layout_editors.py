"""Graphics Designer editors for display sets and workstation layouts.

These widgets expose the existing Qt-free model; they do not create a
second layout document. The tree is the hierarchy, the preview is the
screen/frame geometry, and Save writes through ``LayoutStore``.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.hmi.pvms.layout import (
    DYNAMIC, NAV_FLAT, NAV_HIERARCHICAL, RELOCATE_COPY, RELOCATE_NONE,
    RELOCATE_SWAP, STATIC, DisplayFrame, DisplayNode, DisplaySet,
    HierarchyError, Layout, LayoutStore, Screen,
)
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from azeo_control_trainer.core.presentation.authoring_dialog import (
    AUTHORING_DIALOG_QSS,
    add_authoring_dialog_header,
    mark_primary_action,
    style_dialog_buttons,
)


def _headless() -> bool:
    from azeo_control_trainer.core.presentation.headless import is_headless
    return is_headless()


class DisplaySetEditor(QWidget):
    """Tree editor for the manual's four-level display hierarchy."""

    saved = Signal(str)

    def __init__(self, store: LayoutStore, name: str,
                 displays_provider=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(AUTHORING_DIALOG_QSS)
        self.store = store
        self.configuration_name = name
        self.display_set = store.display_set(name) or DisplaySet(name)
        self._displays = displays_provider or (lambda: ())

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"Display Set - {name}")
        title.setStyleSheet(
            f"color: {WF['navy']}; font-size: 10.5pt; font-weight: 600;")
        root.addWidget(title)
        hint = QLabel(
            "Arrange displays from Overview (L1) through Diagnostics "
            "(L4). Names may be placeholders for displays created later.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {WF['tx2']};")
        root.addWidget(hint)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Display hierarchy", "Level", "State"])
        self.tree.setColumnWidth(0, 260)
        split.addWidget(self.tree)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        self.available = QListWidget()
        self.available.addItems(sorted(set(self._displays())))
        side_lay.addWidget(QLabel("Available displays"))
        side_lay.addWidget(self.available, 1)
        for text, slot in (
                ("Add overview", self._prompt_root),
                ("Add child", self._prompt_child),
                ("Add non-hierarchical", self._prompt_other),
                ("Add placeholder", self._prompt_placeholder),
                ("Remove selected", self.remove_selected)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            side_lay.addWidget(button)
        split.addWidget(side)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        footer = QHBoxLayout()
        self.status = QLabel("")
        footer.addWidget(self.status, 1)
        save = QPushButton("Save Display Set")
        mark_primary_action(save)
        save.clicked.connect(self.save)
        footer.addWidget(save)
        root.addLayout(footer)
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()

        def add(parent, node: DisplayNode):
            item = QTreeWidgetItem(parent, [
                node.display, f"L{node.level}",
                "Placeholder" if node.placeholder else "Display",
            ])
            item.setData(0, Qt.UserRole, node.display)
            for child in node.children:
                add(item, child)
            item.setExpanded(True)

        hierarchy = QTreeWidgetItem(self.tree, ["Hierarchy", "", ""])
        for node in self.display_set.roots:
            add(hierarchy, node)
        hierarchy.setExpanded(True)
        other = QTreeWidgetItem(
            self.tree, ["Non-hierarchical", "Other", ""])
        other.setData(0, Qt.UserRole + 1, "other")
        for name in self.display_set.non_hierarchical:
            item = QTreeWidgetItem(other, [name, "Other", "Display"])
            item.setData(0, Qt.UserRole, name)
        other.setExpanded(True)
        self.status.setText(
            f"{len(self.display_set.displays())} display(s) - "
            f"depth {self.display_set.depth()}/4")

    def add_root(self, display: str, *, placeholder: bool = False):
        node = self.display_set.add_root(display)
        node.placeholder = placeholder
        self.rebuild()
        return node

    def add_child(self, parent_display: str, display: str, *,
                  placeholder: bool = False):
        parent = self.display_set.find(parent_display)
        if parent is None:
            raise HierarchyError(f"parent display {parent_display!r} not found")
        node = self.display_set.add_child(parent, display)
        node.placeholder = placeholder
        self.rebuild()
        return node

    def add_non_hierarchical(self, display: str) -> None:
        self.display_set.add_non_hierarchical(display)
        self.rebuild()

    def remove(self, display: str) -> bool:
        if display in self.display_set.non_hierarchical:
            self.display_set.non_hierarchical.remove(display)
            self.rebuild()
            return True

        def prune(nodes) -> bool:
            for index, node in enumerate(nodes):
                if node.display == display:
                    nodes.pop(index)
                    return True
                if prune(node.children):
                    return True
            return False

        removed = prune(self.display_set.roots)
        if removed:
            self.rebuild()
        return removed

    def remove_selected(self) -> bool:
        item = self.tree.currentItem()
        return self.remove(item.data(0, Qt.UserRole)) \
            if item and item.data(0, Qt.UserRole) else False

    def save(self) -> bool:
        self.store.save_display_set(self.display_set)
        self.status.setText(
            f"Saved {len(self.display_set.displays())} display(s)")
        self.saved.emit(self.display_set.name)
        return True

    def _choice(self, title: str) -> str:
        selected = self.available.currentItem()
        initial = selected.text() if selected else ""
        if _headless():
            return initial
        value, ok = QInputDialog.getText(self, title, "Display name:",
                                         text=initial)
        return value.strip() if ok else ""

    def _show_error(self, error: Exception) -> None:
        self.status.setText(str(error))
        if not _headless():
            QMessageBox.warning(self, "Display set", str(error))

    def _prompt_root(self) -> None:
        value = self._choice("Add overview display")
        if value:
            try:
                self.add_root(value)
            except HierarchyError as error:
                self._show_error(error)

    def _prompt_child(self) -> None:
        selected = self.tree.currentItem()
        parent = selected.data(0, Qt.UserRole) if selected else ""
        value = self._choice("Add child display")
        if value and parent:
            try:
                self.add_child(parent, value)
            except HierarchyError as error:
                self._show_error(error)

    def _prompt_other(self) -> None:
        value = self._choice("Add non-hierarchical display")
        if value:
            try:
                self.add_non_hierarchical(value)
            except HierarchyError as error:
                self._show_error(error)

    def _prompt_placeholder(self) -> None:
        value = self._choice("Add display placeholder")
        if not value:
            return
        selected = self.tree.currentItem()
        parent = selected.data(0, Qt.UserRole) if selected else ""
        try:
            if parent:
                self.add_child(parent, value, placeholder=True)
            else:
                self.add_root(value, placeholder=True)
        except HierarchyError as error:
            self._show_error(error)


class LayoutEditor(QWidget):
    """Screen/frame tree with a scaled visual preview and property form."""

    saved = Signal(str)
    assign_requested = Signal(str)

    def __init__(self, store: LayoutStore, name: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(AUTHORING_DIALOG_QSS)
        self.store = store
        self.configuration_name = name
        self.layout_model = store.layout(name) or Layout(name)
        self._current_frame = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"Layout - {name}")
        title.setStyleSheet(
            f"color: {WF['navy']}; font-size: 10.5pt; font-weight: 600;")
        root.addWidget(title)
        styling = QHBoxLayout()
        styling.addWidget(QLabel("Standard blink"))
        self.standard_blink = QSpinBox()
        self.standard_blink.setRange(50, 5000)
        self.standard_blink.setSuffix(" ms")
        self.standard_blink.setValue(self.layout_model.blink_standard_ms)
        styling.addWidget(self.standard_blink)
        styling.addWidget(QLabel("Alternate blink"))
        self.alternate_blink = QSpinBox()
        self.alternate_blink.setRange(50, 5000)
        self.alternate_blink.setSuffix(" ms")
        self.alternate_blink.setValue(self.layout_model.blink_alternate_ms)
        styling.addWidget(self.alternate_blink)
        styling.addStretch(1)
        root.addLayout(styling)
        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._selection_changed)
        split.addWidget(self.tree)
        self.preview_scene = QGraphicsScene(self)
        self.preview = QGraphicsView(self.preview_scene)
        self.preview.setMinimumWidth(420)
        split.addWidget(self.preview)
        properties = QWidget()
        form = QFormLayout(properties)
        self.frame_name = QLineEdit()
        self.frame_kind = AuthoringComboBox()
        self.frame_kind.addItem("Dynamic", DYNAMIC)
        self.frame_kind.addItem("Static", STATIC)
        self.frame_levels = QLineEdit()
        self.frame_levels.setPlaceholderText("1, 2, 3; empty = other")
        self.frame_initial = QLineEdit()
        self.frame_route = QLineEdit()
        self.frame_navigation = QCheckBox("Show navigation bar")
        self.frame_navigation_style = AuthoringComboBox()
        self.frame_navigation_style.addItem("Hierarchy", NAV_HIERARCHICAL)
        self.frame_navigation_style.addItem("Flat list", NAV_FLAT)
        self.frame_coordinates = QCheckBox(
            "Coordinate this frame with hierarchy changes")
        self.frame_prevent_external = QCheckBox(
            "Keep navigation from this frame local")
        self.frame_relocation = AuthoringComboBox()
        self.frame_relocation.addItem("Do not relocate", RELOCATE_NONE)
        self.frame_relocation.addItem("Swap existing displays", RELOCATE_SWAP)
        self.frame_relocation.addItem("Copy into target frame", RELOCATE_COPY)
        self.frame_rect = []
        for label, widget in (
                ("Frame name", self.frame_name),
                ("Frame type", self.frame_kind),
                ("Auto-open levels", self.frame_levels),
                ("Initial display", self.frame_initial),
                ("Target display frame", self.frame_route),
                ("Navigation style", self.frame_navigation_style),
                ("Coordination relocation", self.frame_relocation)):
            form.addRow(label, widget)
        form.addRow(self.frame_navigation)
        form.addRow(self.frame_coordinates)
        form.addRow(self.frame_prevent_external)
        for name, value in zip(("X", "Y", "Width", "Height"),
                               (0.0, 0.0, 1.0, 1.0)):
            field = QDoubleSpinBox()
            field.setRange(0.0, 1.0)
            field.setDecimals(3)
            field.setSingleStep(0.025)
            field.setValue(value)
            self.frame_rect.append(field)
            form.addRow(name, field)
        apply_button = QPushButton("Apply Frame Properties")
        apply_button.clicked.connect(self.apply_frame)
        form.addRow(apply_button)
        split.addWidget(properties)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        buttons = QHBoxLayout()
        for text, slot in (
                ("Add Screen", self._prompt_screen),
                ("Add Frame", self._prompt_frame),
                ("Remove Frame", self.remove_selected_frame)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        assign = QPushButton("Assign to Workstation…")
        assign.clicked.connect(
            lambda: self.assign_requested.emit(self.layout_model.name))
        buttons.addWidget(assign)
        self.status = QLabel("")
        buttons.addWidget(self.status, 1)
        save = QPushButton("Save Layout")
        mark_primary_action(save)
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for screen in self.layout_model.screens:
            screen_item = QTreeWidgetItem(
                self.tree, [f"{screen.name} - {screen.width}x{screen.height}"])
            screen_item.setData(0, Qt.UserRole + 1, screen.name)
            for frame in screen.frames:
                item = QTreeWidgetItem(screen_item, [frame.name])
                item.setData(0, Qt.UserRole, frame.name)
                item.setData(0, Qt.UserRole + 1, screen.name)
            screen_item.setExpanded(True)
        self._paint_preview()
        self.status.setText(
            f"{len(self.layout_model.screens)} screen(s), "
            f"{len(self.layout_model.frames())} frame(s)")

    def _paint_preview(self) -> None:
        self.preview_scene.clear()
        y_offset = 0.0
        for screen in self.layout_model.screens:
            scale = min(520.0 / max(screen.width, 1),
                        290.0 / max(screen.height, 1))
            width, height = screen.width * scale, screen.height * scale
            screen_rect = QRectF(0, y_offset, width, height)
            self.preview_scene.addRect(
                screen_rect, QPen(QColor(WF["navy"]), 2),
                QBrush(QColor(WF["pane"])))
            for frame in screen.frames:
                x, y, w, h = frame.rect
                frame_rect = QRectF(x * width, y_offset + y * height,
                                    w * width, h * height)
                colour = QColor(WF["chrome"] if frame.is_static
                                else WF["page"])
                self.preview_scene.addRect(
                    frame_rect, QPen(QColor(WF["lapis"]), 1),
                    QBrush(colour))
                label = QGraphicsSimpleTextItem(frame.name)
                label.setBrush(QBrush(QColor(WF["tx"])))
                label.setPos(frame_rect.left() + 4, frame_rect.top() + 3)
                self.preview_scene.addItem(label)
            y_offset += height + 20
        self.preview_scene.setSceneRect(
            self.preview_scene.itemsBoundingRect().adjusted(-5, -5, 5, 5))

    def add_screen(self, name: str, width: int = 1920,
                   height: int = 1080) -> Screen:
        if any(screen.name == name for screen in self.layout_model.screens):
            raise ValueError(f"screen {name!r} already exists")
        screen = self.layout_model.add_screen(Screen(name, width, height))
        self.rebuild()
        return screen

    def add_frame(self, screen_name: str, name: str, *,
                  rect=(0.0, 0.0, 1.0, 1.0), kind=DYNAMIC,
                  levels=()) -> DisplayFrame:
        screen = next((one for one in self.layout_model.screens
                       if one.name == screen_name), None)
        if screen is None:
            raise ValueError(f"screen {screen_name!r} not found")
        if self.layout_model.frame(name) is not None:
            raise ValueError(f"frame {name!r} already exists")
        frame = screen.add_frame(
            DisplayFrame(name, tuple(rect), kind, tuple(levels)))
        self.rebuild()
        return frame

    def apply_frame(self) -> bool:
        frame = self.layout_model.frame(self._current_frame)
        if frame is None:
            return False
        new_name = self.frame_name.text().strip()
        if new_name and new_name != frame.name \
                and self.layout_model.frame(new_name) is not None:
            self.status.setText(f"Frame {new_name!r} already exists")
            return False
        frame.name = new_name or frame.name
        frame.kind = self.frame_kind.currentData() or DYNAMIC
        try:
            frame.levels = tuple(
                int(value.strip())
                for value in self.frame_levels.text().split(",")
                if value.strip())
        except ValueError:
            self.status.setText("Levels must be comma-separated 1..4")
            return False
        if any(level not in (1, 2, 3, 4) for level in frame.levels):
            self.status.setText("Levels must be between 1 and 4")
            return False
        frame.initial_display = self.frame_initial.text().strip()
        frame.routes_to = self.frame_route.text().strip()
        frame.navigation_bar = self.frame_navigation.isChecked()
        frame.navigation_style = (
            self.frame_navigation_style.currentData() or NAV_HIERARCHICAL)
        frame.coordinates = self.frame_coordinates.isChecked()
        frame.prevent_external_coordination = (
            self.frame_prevent_external.isChecked())
        frame.relocation = self.frame_relocation.currentData() or RELOCATE_NONE
        frame.rect = tuple(field.value() for field in self.frame_rect)
        self._current_frame = frame.name
        self.rebuild()
        return True

    def remove_selected_frame(self) -> bool:
        frame = self.layout_model.frame(self._current_frame)
        if frame is None:
            return False
        for screen in self.layout_model.screens:
            if frame in screen.frames:
                screen.frames.remove(frame)
                self._current_frame = ""
                self.rebuild()
                return True
        return False

    def save(self) -> bool:
        self.layout_model.blink_standard_ms = self.standard_blink.value()
        self.layout_model.blink_alternate_ms = self.alternate_blink.value()
        problems = self.validate()
        if problems:
            self.status.setText(problems[0])
            return False
        self.store.save_layout(self.layout_model)
        self.status.setText("Saved")
        self.saved.emit(self.layout_model.name)
        return True

    def validate(self) -> list[str]:
        problems = []
        names = [frame.name for frame in self.layout_model.frames()]
        if len(names) != len(set(names)):
            problems.append("Frame names must be unique across the layout")
        for frame in self.layout_model.frames():
            x, y, width, height = frame.rect
            if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                problems.append(f"Frame {frame.name}: geometry leaves its screen")
            if frame.routes_to and self.layout_model.frame(
                    frame.routes_to) is None:
                problems.append(
                    f"Frame {frame.name}: target {frame.routes_to!r} not found")
        return problems

    def _selection_changed(self, current, _previous) -> None:
        name = current.data(0, Qt.UserRole) if current else ""
        frame = self.layout_model.frame(name) if name else None
        self._current_frame = frame.name if frame else ""
        if frame is None:
            return
        self.frame_name.setText(frame.name)
        self.frame_kind.setCurrentIndex(max(
            self.frame_kind.findData(frame.kind), 0))
        self.frame_levels.setText(", ".join(map(str, frame.levels)))
        self.frame_initial.setText(frame.initial_display)
        self.frame_route.setText(frame.routes_to)
        self.frame_navigation.setChecked(frame.navigation_bar)
        self.frame_navigation_style.setCurrentIndex(max(
            self.frame_navigation_style.findData(frame.navigation_style), 0))
        self.frame_coordinates.setChecked(frame.coordinates)
        self.frame_prevent_external.setChecked(
            frame.prevent_external_coordination)
        self.frame_relocation.setCurrentIndex(max(
            self.frame_relocation.findData(frame.relocation), 0))
        for field, value in zip(self.frame_rect, frame.rect):
            field.setValue(float(value))

    def _prompt_screen(self) -> None:
        if _headless():
            return
        name, ok = QInputDialog.getText(self, "Add screen", "Screen name:")
        if ok and name.strip():
            try:
                self.add_screen(name.strip())
            except ValueError as error:
                QMessageBox.warning(self, "Layout", str(error))

    def _prompt_frame(self) -> None:
        selected = self.tree.currentItem()
        screen_name = selected.data(0, Qt.UserRole + 1) if selected else ""
        if not screen_name or _headless():
            return
        name, ok = QInputDialog.getText(self, "Add frame", "Frame name:")
        if ok and name.strip():
            try:
                self.add_frame(screen_name, name.strip())
            except ValueError as error:
                QMessageBox.warning(self, "Layout", str(error))


class WorkstationAssignmentDialog(QDialog):
    """Assign one layout and selectable display sets to a workstation."""

    saved = Signal(str)

    def __init__(self, store: LayoutStore, *, layout_name: str = "",
                 workstation: str = "", parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Workstation Graphics Assignment")
        self.resize(520, 440)
        root = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            root,
            "Workstation assignment",
            "Choose the operator layout, available display sets and the "
            "initial navigation context.",
        )
        form = QFormLayout()
        self.workstation = QLineEdit(workstation)
        self.workstation.setPlaceholderText("for example CON-01")
        self.layout_choice = AuthoringComboBox()
        self.layout_choice.addItem("No layout", "")
        for layout in store.layouts():
            self.layout_choice.addItem(layout.name, layout.name)
        if layout_name:
            self.layout_choice.setCurrentIndex(max(
                0, self.layout_choice.findData(layout_name)))
        form.addRow("Workstation", self.workstation)
        form.addRow("Layout", self.layout_choice)
        root.addLayout(form)
        root.addWidget(QLabel("Selectable display sets"))
        self.set_choices = QListWidget()
        for display_set in store.display_sets():
            item = QListWidgetItem(display_set.name, self.set_choices)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        self.set_choices.itemChanged.connect(self._refresh_active_sets)
        root.addWidget(self.set_choices, 1)
        active_row = QFormLayout()
        self.active_choice = AuthoringComboBox()
        active_row.addRow("Initial display set", self.active_choice)
        root.addLayout(active_row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_saved)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Save)
        root.addWidget(buttons)
        if workstation:
            self.load_assignment(workstation)
        self._refresh_active_sets()

    def checked_sets(self) -> tuple[str, ...]:
        return tuple(
            self.set_choices.item(index).text()
            for index in range(self.set_choices.count())
            if self.set_choices.item(index).checkState() == Qt.Checked)

    def choose_sets(self, names) -> None:
        selected = set(names)
        for index in range(self.set_choices.count()):
            item = self.set_choices.item(index)
            item.setCheckState(
                Qt.Checked if item.text() in selected else Qt.Unchecked)
        self._refresh_active_sets()

    def load_assignment(self, workstation: str) -> None:
        assignment = self.store.assignment(workstation)
        self.workstation.setText(workstation)
        self.layout_choice.setCurrentIndex(max(
            0, self.layout_choice.findData(assignment.layout)))
        self.choose_sets(assignment.display_sets)
        index = self.active_choice.findData(assignment.active_display_set)
        if index >= 0:
            self.active_choice.setCurrentIndex(index)

    def _refresh_active_sets(self) -> None:
        current = self.active_choice.currentData()
        self.active_choice.clear()
        for name in self.checked_sets():
            self.active_choice.addItem(name, name)
        index = self.active_choice.findData(current)
        if index >= 0:
            self.active_choice.setCurrentIndex(index)

    def save(self) -> bool:
        try:
            assignment = self.store.assign(
                self.workstation.text().strip(),
                layout=self.layout_choice.currentData() or "",
                display_sets=self.checked_sets(),
                active_display_set=self.active_choice.currentData() or "")
        except ValueError as error:
            self.status.setText(str(error))
            return False
        self.status.setText(f"Saved assignment for {assignment.workstation}")
        self.saved.emit(assignment.workstation)
        return True

    def _accept_if_saved(self) -> None:
        if self.save():
            self.accept()
