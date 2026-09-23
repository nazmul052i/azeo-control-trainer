"""Online SFC diagnostic surface.

The dialog edits no chart configuration.  Every operation targets the live
``SfcChartBlock`` debug state, which is intentionally reset by a download.
That distinction keeps a troubleshooting disable from becoming tomorrow's
saved control strategy.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import SfcChartBlock
from azeo_control_trainer.core.presentation.headless import is_headless


class SfcDebugDialog(QDialog):
    """Inspect and operate one downloaded SFC chart level."""

    ROLE_KIND = Qt.UserRole
    ROLE_ID = Qt.UserRole + 1

    def __init__(self, block: SfcChartBlock, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.block = block
        self.setWindowTitle(f"SFC Online Debug — {block.instance_name}")
        self.setMinimumSize(690, 500)

        root = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setStyleSheet(
            "font-weight: 600; color: #24364B; padding: 4px 2px;")
        root.addWidget(self.status)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Chart element", "Type", "Runtime state", "Last evaluation"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 130)
        root.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.stop_start = QPushButton()
        self.stop_start.clicked.connect(self._toggle_level)
        buttons.addWidget(self.stop_start)
        reset = QPushButton("Reset Level")
        reset.clicked.connect(self._reset_level)
        buttons.addWidget(reset)
        self.toggle_disable = QPushButton("Disable Selected")
        self.toggle_disable.clicked.connect(self._toggle_selected)
        buttons.addWidget(self.toggle_disable)
        self.force = QPushButton("Force Transition Once")
        self.force.clicked.connect(self._force_selected)
        buttons.addWidget(self.force)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.tree.currentItemChanged.connect(lambda *_: self._sync_actions())
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def _selected(self) -> tuple[str, str] | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        kind = item.data(0, self.ROLE_KIND)
        element_id = item.data(0, self.ROLE_ID)
        if not kind or not element_id:
            return None
        return str(kind), str(element_id)

    def _is_disabled(self, kind: str, element_id: str) -> bool:
        values = {
            "step": self.block._disabled_steps,
            "action": self.block._disabled_actions,
            "transition": self.block._disabled_transitions,
        }.get(kind, set())
        return element_id in values

    def refresh(self) -> None:
        selected = self._selected()
        snapshot = self.block.sequence_snapshot()
        evaluations = {
            row["id"]: row for row in snapshot.get("transitionEvaluations", [])
        }
        self.status.setText(
            f"{snapshot['mode']}  •  Active: "
            f"{snapshot.get('activeStepId') or '—'}  •  "
            f"Step time: {snapshot.get('elapsed', 0.0):.2f} s")
        self.stop_start.setText(
            "Start Level" if self.block.debug_stopped else "Stop Level")

        self.tree.blockSignals(True)
        self.tree.clear()
        chart = self.block.chart()
        for step in chart.steps:
            state = ("ACTIVE" if step.id == self.block._active else
                     "DISABLED" if step.id in self.block._disabled_steps else
                     "COMPLETE" if step.id in self.block._completed else
                     "PENDING")
            step_item = self._item(
                step.name or step.id, "Step", state, "", "step", step.id)
            self.tree.addTopLevelItem(step_item)
            for action in step.actions:
                action_state = (
                    "DISABLED" if action.id in self.block._disabled_actions else
                    "STORED" if (action.name or action.id)
                    in self.block._stored_actions else "ENABLED")
                step_item.addChild(self._item(
                    action.name or action.id,
                    f"Action {action.qualifier}", action_state, "",
                    "action", action.id))
            for transition in chart.leaving(step.id):
                row = evaluations.get(transition.id, {})
                if transition.id in self.block._disabled_transitions:
                    transition_state = "DISABLED"
                elif row.get("fired"):
                    transition_state = "FIRED"
                else:
                    transition_state = "ENABLED"
                if row.get("error"):
                    result = f"ERROR: {row['error']}"
                elif transition.id in evaluations:
                    result = "TRUE" if row.get("value") else "FALSE"
                    if row.get("forced"):
                        result += " (FORCED)"
                else:
                    result = "not evaluated"
                step_item.addChild(self._item(
                    transition.name or transition.id, "Transition",
                    transition_state, result, "transition", transition.id))
            step_item.setExpanded(True)
        self.tree.blockSignals(False)

        if selected is not None:
            iterator = self.tree.invisibleRootItem()
            stack = [iterator.child(i) for i in range(iterator.childCount())]
            while stack:
                item = stack.pop(0)
                if (item.data(0, self.ROLE_KIND),
                        item.data(0, self.ROLE_ID)) == selected:
                    self.tree.setCurrentItem(item)
                    break
                stack.extend(item.child(i) for i in range(item.childCount()))
        self._sync_actions()

    def _item(self, name: str, type_name: str, state: str, result: str,
              kind: str, element_id: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name, type_name, state, result])
        item.setData(0, self.ROLE_KIND, kind)
        item.setData(0, self.ROLE_ID, element_id)
        return item

    def _sync_actions(self) -> None:
        selected = self._selected()
        enabled = selected is not None
        self.toggle_disable.setEnabled(enabled)
        self.force.setEnabled(bool(selected and selected[0] == "transition"))
        if selected:
            disabled = self._is_disabled(*selected)
            self.toggle_disable.setText(
                "Enable Selected" if disabled else "Disable Selected")
        else:
            self.toggle_disable.setText("Disable Selected")

    def _toggle_level(self) -> None:
        if self.block.debug_stopped:
            self.block.debug_start()
        else:
            self.block.debug_stop()
        self.refresh()

    def _reset_level(self) -> None:
        self.block.debug_reset()
        self.refresh()

    def _toggle_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        kind, element_id = selected
        disable = not self._is_disabled(kind, element_id)
        operation = {
            "step": self.block.debug_disable_step,
            "action": self.block.debug_disable_action,
            "transition": self.block.debug_disable_transition,
        }.get(kind)
        if operation is not None:
            operation(element_id, disable)
        self.refresh()

    def _force_selected(self) -> None:
        selected = self._selected()
        if selected is None or selected[0] != "transition":
            return
        if not self.block.debug_force_transition(selected[1]) and not is_headless():
            QMessageBox.information(
                self, "Transition Not Forced",
                "Only an enabled transition leaving the active step can be "
                "forced. The request was not queued.")
        self.refresh()
