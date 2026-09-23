"""The SFC chart editor — steps and transitions as a chart, not blocks.

Canvas interaction adapted from azeo_dcs's `sfc_canvas.py` (same author):
step boxes with top/bottom ports, orthogonal transition connectors whose
captions sit beside a vertical run (centred text over the line reads as
the chart running through it), positions persisted on the steps.

The right-hand structure panel is the authoring surface: add/edit/remove
steps and transitions, actions with their IEC SFC qualifiers, and every
expression — action or condition — is written in the trainer's expression
editor with its parameter browsers and Parse button, because a sequence
condition with a typo is a chart that never advances and never says why.

Validation is the model's own `Chart.problems()` — unknown endpoints,
conditionless transitions, unreachable steps — surfaced before OK, the
same edit-time honesty the CND editor has.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGraphicsPathItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QDoubleSpinBox, QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import (
    QUALIFIERS, Chart, ChartAction, ChartStep, ChartTransition,
)

STEP_W, STEP_H = 190.0, 58.0
COL_X, ROW_GAP = 120.0, 120.0


class _StepItem(QGraphicsRectItem):
    def __init__(self, editor, step: ChartStep):
        super().__init__(0, 0, STEP_W, STEP_H)
        self._editor = editor
        self.step = step
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPen(QPen(QColor("#8FA6BC"), 1.2))
        self.setBrush(QBrush(QColor("#FFFFFF")))
        stripe = QGraphicsRectItem(0, 0, 6, STEP_H, self)
        stripe.setPen(Qt.NoPen)
        stripe.setBrush(QColor("#2E7D32" if step.initial else "#1D6FBF"))
        stripe.setAcceptedMouseButtons(Qt.NoButton)
        title = QGraphicsSimpleTextItem(step.name or step.id, self)
        title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        title.setBrush(QColor("#16324F"))
        title.setPos(14, 8)
        title.setAcceptedMouseButtons(Qt.NoButton)
        actions = QGraphicsSimpleTextItem(
            f"{len(step.actions)} action"
            f"{'s' if len(step.actions) != 1 else ''}", self)
        actions.setFont(QFont("Segoe UI", 7))
        actions.setBrush(QColor("#7A8494"))
        actions.setPos(14, 34)
        actions.setAcceptedMouseButtons(Qt.NoButton)
        self.setPos(step.x, step.y)

    def port(self, role: str) -> QPointF:
        offset = QPointF(STEP_W / 2, 0 if role == "in" else STEP_H)
        return self.mapToScene(offset)

    def itemChange(self, change, value):            # noqa: N802
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            self.step.x = self.pos().x()
            self.step.y = self.pos().y()
            self._editor.reroute()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):         # noqa: N802
        self._editor.edit_step(self.step.id)


class _TransitionItem(QGraphicsPathItem):
    def __init__(self, editor, transition: ChartTransition,
                 source: _StepItem, target: _StepItem | None):
        super().__init__()
        self._editor = editor
        self.transition = transition
        self.source_item = source
        self.target_item = target
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1)
        self.setPen(QPen(QColor("#5A6A7A"), 2.0))
        self.label = QGraphicsSimpleTextItem(self)
        self.label.setFont(QFont("Consolas", 8))
        self.label.setBrush(QColor("#7A5CB8"))
        self.label.setAcceptedMouseButtons(Qt.NoButton)
        self.update_path()

    def update_path(self) -> None:
        start = self.source_item.port("out")
        end = (self.target_item.port("in") if self.target_item is not None
               else start + QPointF(0, 56))
        middle = start.y() + max(24.0, (end.y() - start.y()) / 2.0)
        path = QPainterPath(start)
        path.lineTo(start.x(), middle)
        if self.target_item is not None:
            path.lineTo(end.x(), middle)
            path.lineTo(end)
        else:
            # Termination: the manual's last transition — a short stub
            # with a double bar underneath.
            path.lineTo(start.x(), middle + 22)
            path.moveTo(start.x() - 16, middle + 26)
            path.lineTo(start.x() + 16, middle + 26)
            path.moveTo(start.x() - 16, middle + 31)
            path.lineTo(start.x() + 16, middle + 31)
        self.setPath(path)
        condition = self.transition.condition.strip() or "(no condition)"
        self.label.setText(condition[:44])
        self.label.setToolTip(condition)
        if self.target_item is None \
                or abs(start.x() - end.x()) < 1.0:
            self.label.setPos(start.x() + 12,
                              middle - self.label.boundingRect().height()
                              / 2)
        else:
            self.label.setPos((start.x() + end.x()) / 2
                              - self.label.boundingRect().width() / 2,
                              middle - self.label.boundingRect().height()
                              - 4)

    def mouseDoubleClickEvent(self, event):         # noqa: N802
        self._editor.edit_transition(self.transition.id)


class SfcChartEditorDialog(QDialog):
    """Author a chart; OK applies it to the SFC_CHART block."""

    applied = Signal(object)                        # Chart

    def __init__(self, block, graph=None, store=None, parent=None):
        super().__init__(parent)
        self._block = block
        self._graph = graph
        self._store = store
        self.chart = Chart.from_dict(block.chart().to_dict())
        module = getattr(graph, "name", "") or "MODULE"
        self.setWindowTitle(f"{module}/{block.instance_name} — SFC Chart")
        self.resize(940, 620)

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing, True)
        self.view.setBackgroundBrush(QColor("#F2F4F8"))
        split.addWidget(self.view)

        side = QWidget()
        lay = QVBoxLayout(side)
        lay.addWidget(QLabel("Steps"))
        self.step_list = QListWidget()
        self.step_list.itemDoubleClicked.connect(
            lambda item: self.edit_step(item.data(0x0100)))
        lay.addWidget(self.step_list, 2)
        row = QHBoxLayout()
        for text, slot in (("Add Step", self.add_step),
                           ("Remove", self.remove_step)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        lay.addLayout(row)

        lay.addWidget(QLabel("Transitions"))
        self.transition_list = QListWidget()
        self.transition_list.itemDoubleClicked.connect(
            lambda item: self.edit_transition(item.data(0x0100)))
        lay.addWidget(self.transition_list, 2)
        row = QHBoxLayout()
        for text, slot in (("Add Transition", self.add_transition),
                           ("Remove", self.remove_transition)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        lay.addLayout(row)

        lay.addWidget(QLabel("Validation"))
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.problems.setMaximumHeight(90)
        lay.addWidget(self.problems)
        split.addWidget(side)
        split.setSizes([620, 320])
        root.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel
                                   | QDialogButtonBox.Help)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(self.open_help)
        help_button = buttons.button(QDialogButtonBox.Help)
        help_button.setToolTip("SFC chart reference — steps, transitions, "
                               "qualifiers, the expression language (F1)")
        self._shortcuts = [
            QShortcut(QKeySequence(Qt.Key_F1), self, self.open_help),
        ]
        root.addWidget(buttons)
        self.rebuild()

    # ------------------------------------------------------------- drawing
    def _auto_layout(self) -> None:
        """A vertical chart when nobody has placed anything yet."""
        if any(step.x or step.y for step in self.chart.steps):
            return
        ordered: list[str] = []
        start = self.chart.initial_step()
        queue = [start.id] if start else []
        seen = set(queue)
        while queue:
            step_id = queue.pop(0)
            ordered.append(step_id)
            for transition in self.chart.leaving(step_id):
                if transition.target and transition.target not in seen:
                    seen.add(transition.target)
                    queue.append(transition.target)
        for step in self.chart.steps:
            if step.id not in ordered:
                ordered.append(step.id)
        for index, step_id in enumerate(ordered):
            step = self.chart.step(step_id)
            step.x = COL_X
            step.y = 40.0 + index * ROW_GAP

    def rebuild(self) -> None:
        self._auto_layout()
        self.scene.clear()
        self._step_items: dict[str, _StepItem] = {}
        self._transition_items: list[_TransitionItem] = []
        for step in self.chart.steps:
            item = _StepItem(self, step)
            self.scene.addItem(item)
            self._step_items[step.id] = item
        for transition in self.chart.transitions:
            source = self._step_items.get(transition.source)
            if source is None:
                continue
            target = self._step_items.get(transition.target) \
                if transition.target else None
            item = _TransitionItem(self, transition, source, target)
            self.scene.addItem(item)
            self._transition_items.append(item)

        self.step_list.clear()
        for step in self.chart.steps:
            text = (step.name or step.id) \
                + ("  [initial]" if step.initial else "") \
                + f"  ({len(step.actions)} action(s))"
            item = QListWidgetItem(text)
            item.setData(0x0100, step.id)
            self.step_list.addItem(item)
        self.transition_list.clear()
        for transition in self.chart.transitions:
            target = transition.target or "(terminate)"
            item = QListWidgetItem(
                f"{transition.source} → {target}: "
                f"{transition.condition[:40] or '(no condition)'}")
            item.setData(0x0100, transition.id)
            self.transition_list.addItem(item)
        self.validate()

    def reroute(self) -> None:
        for item in getattr(self, "_transition_items", []):
            item.update_path()

    def validate(self) -> list[str]:
        found = self.chart.problems()
        self.problems.setPlainText(
            "\n".join(found) if found else "No problems detected.")
        self.problems.setStyleSheet(
            "color: #C62828;" if found else "color: #2E7D32;")
        return found

    # ---------------------------------------------------------- expressions
    def _edit_expression(self, initial: str) -> str | None:
        """The trainer's expression editor, seeded with `initial`."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        from .expression_editor import ExpressionEditorDialog

        dialog = ExpressionEditorDialog(self._block, self._graph,
                                        self._store, parent=self)
        dialog.editor.setPlainText(initial)
        result: list[str] = []
        dialog.applied.connect(result.append)
        self._expression_dialog = dialog
        if not is_headless():
            dialog.exec()
        return result[0] if result else None

    # ---------------------------------------------------------------- steps
    def _new_id(self, prefix: str, existing) -> str:
        index = 1
        taken = {x.id for x in existing}
        while f"{prefix}{index}" in taken:
            index += 1
        return f"{prefix}{index}"

    def add_step(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Step", "Step name:")
        if not ok or not name.strip():
            return
        step = ChartStep(self._new_id("s", self.chart.steps),
                         name=name.strip(),
                         initial=not self.chart.steps)
        self.chart.steps.append(step)
        self.rebuild()

    def remove_step(self) -> None:
        item = self.step_list.currentItem()
        if item is None:
            return
        step_id = item.data(0x0100)
        self.chart.steps = [s for s in self.chart.steps
                            if s.id != step_id]
        self.chart.transitions = [
            t for t in self.chart.transitions
            if t.source != step_id and t.target != step_id]
        self.rebuild()

    def edit_step(self, step_id: str) -> None:
        step = self.chart.step(step_id)
        if step is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Step — {step.name or step.id}")
        lay = QVBoxLayout(dialog)
        name = QLineEdit(step.name)
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        row.addWidget(name, 1)
        initial = QCheckBox("Initial step")
        initial.setChecked(step.initial)
        row.addWidget(initial)
        lay.addLayout(row)

        lay.addWidget(QLabel("Actions (double-click the expression to "
                             "edit it in the expression editor):"))
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            ["Name", "Qualifier", "Time (s)", "Reset target", "Expression"])
        table.setColumnWidth(3, 120)
        table.setColumnWidth(4, 300)

        def reload_actions():
            table.blockSignals(True)
            table.setRowCount(0)
            for action in step.actions:
                row_index = table.rowCount()
                table.insertRow(row_index)
                table.setItem(row_index, 0,
                              QTableWidgetItem(action.name or action.id))
                qualifier = AuthoringComboBox()
                for key, doc in QUALIFIERS.items():
                    qualifier.addItem(key)
                    qualifier.setItemData(qualifier.count() - 1, doc,
                                          Qt.ToolTipRole)
                qualifier.setCurrentText(action.qualifier)
                qualifier.currentTextChanged.connect(
                    lambda text, a=action: setattr(a, "qualifier", text))
                table.setCellWidget(row_index, 1, qualifier)
                duration = QDoubleSpinBox()
                duration.setRange(0.0, 86400.0)
                duration.setDecimals(3)
                duration.setValue(float(action.time_s or 0.0))
                duration.setToolTip(
                    "Used by L (maximum duration) and D (start delay)")
                duration.valueChanged.connect(
                    lambda value, a=action: setattr(a, "time_s", value))
                table.setCellWidget(row_index, 2, duration)
                table.setItem(row_index, 3, QTableWidgetItem(action.target))
                cell = QTableWidgetItem(action.expression)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                table.setItem(row_index, 4, cell)
            table.blockSignals(False)

        def cell_changed(row_index, column):
            if row_index >= len(step.actions):
                return
            item = table.item(row_index, column)
            if item is None:
                return
            if column == 0:
                step.actions[row_index].name = item.text().strip()
            elif column == 3:
                step.actions[row_index].target = item.text().strip()

        table.cellChanged.connect(cell_changed)

        def cell_open(row_index, column):
            if column != 4 or row_index >= len(step.actions):
                return
            action = step.actions[row_index]
            text = self._edit_expression(action.expression)
            if text is not None:
                action.expression = text
                reload_actions()

        table.cellDoubleClicked.connect(cell_open)
        reload_actions()
        lay.addWidget(table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add Action")

        def add_action():
            step.actions.append(ChartAction(
                self._new_id("a", step.actions), qualifier="P"))
            reload_actions()

        add.clicked.connect(add_action)
        remove = QPushButton("Remove Action")

        def remove_action():
            row_index = table.currentRow()
            if 0 <= row_index < len(step.actions):
                step.actions.pop(row_index)
                reload_actions()

        remove.clicked.connect(remove_action)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)

        def apply_step():
            step.name = name.text().strip()
            if initial.isChecked():
                for other in self.chart.steps:
                    other.initial = other is step
            dialog.accept()

        buttons.accepted.connect(apply_step)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        dialog.resize(640, 420)
        self._step_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()
        self.rebuild()

    # ----------------------------------------------------------- transitions
    def add_transition(self) -> None:
        if not self.chart.steps:
            return
        transition = ChartTransition(
            self._new_id("t", self.chart.transitions),
            source=self.chart.steps[0].id,
            target=(self.chart.steps[1].id
                    if len(self.chart.steps) > 1 else ""))
        self.chart.transitions.append(transition)
        self.rebuild()
        self.edit_transition(transition.id)

    def remove_transition(self) -> None:
        item = self.transition_list.currentItem()
        if item is None:
            return
        transition_id = item.data(0x0100)
        self.chart.transitions = [t for t in self.chart.transitions
                                  if t.id != transition_id]
        self.rebuild()

    def edit_transition(self, transition_id: str) -> None:
        transition = next((t for t in self.chart.transitions
                           if t.id == transition_id), None)
        if transition is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Transition — {transition.id}")
        lay = QVBoxLayout(dialog)
        row = QHBoxLayout()
        row.addWidget(QLabel("From:"))
        source = AuthoringComboBox()
        row.addWidget(source, 1)
        row.addWidget(QLabel("To:"))
        target = AuthoringComboBox()
        target.addItem("(terminate)", "")
        row.addWidget(target, 1)
        for step in self.chart.steps:
            source.addItem(step.name or step.id, step.id)
            target.addItem(step.name or step.id, step.id)
        source.setCurrentIndex(max(0, source.findData(transition.source)))
        target.setCurrentIndex(max(0, target.findData(transition.target)))
        lay.addLayout(row)
        condition = QLineEdit(transition.condition)
        condition.setReadOnly(True)
        edit = QPushButton("Edit Condition…")

        def open_condition():
            text = self._edit_expression(transition.condition)
            if text is not None:
                transition.condition = text
                condition.setText(text)

        edit.clicked.connect(open_condition)
        row = QHBoxLayout()
        row.addWidget(QLabel("Condition:"))
        row.addWidget(condition, 1)
        row.addWidget(edit)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)

        def apply_transition():
            transition.source = source.currentData()
            transition.target = target.currentData()
            dialog.accept()

        buttons.accepted.connect(apply_transition)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        dialog.resize(560, 160)
        self._transition_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()
        self.rebuild()

    # ----------------------------------------------------------------- help
    def open_help(self) -> None:
        """The SFC chart reference, non-modal so it sits beside the chart
        while authoring. Same content as Help ▸ SFC Programming — one
        topic, written once (`SFC_CHART_HELP`)."""
        from azeo_control_trainer.core.presentation.headless import is_headless
        from .help_dialog import SFC_CHART_HELP, styled_help_html

        existing = getattr(self, "_help_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("SFC Chart Help")
        dialog.setModal(False)
        dialog.resize(760, 620)
        lay = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(styled_help_html(SFC_CHART_HELP))
        lay.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        lay.addWidget(buttons)
        self._help_dialog = dialog
        if not is_headless():
            dialog.show()

    # ---------------------------------------------------------------- apply
    def _apply(self) -> None:
        if self.validate():
            return                       # problems stand between us and OK
        self.applied.emit(self.chart)
        self.accept()
