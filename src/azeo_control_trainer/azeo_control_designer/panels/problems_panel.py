"""Continuously updated validation findings with safe, undoable quick fixes."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.menu_style import retain_menu, studio_menu
from azeo_control_trainer.core.strategy.engine.validator import (
    ValidationResult,
    validate_strategy,
)


@dataclass(frozen=True)
class QuickFix:
    key: str
    label: str
    destructive: bool = False


def quick_fixes_for(graph, finding: ValidationResult) -> list[QuickFix]:
    """Describe only fixes whose preconditions are still true in ``graph``."""
    message = finding.message.lower()
    block = graph.blocks.get(finding.block_id)
    fixes: list[QuickFix] = []
    if block is not None:
        if "invalid output limits" in message:
            fixes.append(QuickFix("normalize_pid_limits", "Normalize output limits"))
        if "cascade slave" in message and "no bkcal wire" in message:
            fixes.append(QuickFix("add_bkcal", "Add required BKCAL connection"))
        if "no tag configured" in message or "no plc control tag" in message:
            fixes.append(QuickFix("configure_block", "Configure tag…"))
        if "no process variable input" in message:
            fixes.append(QuickFix("focus_input", "Show required IN pin"))
        if "no cascade input" in message:
            fixes.append(QuickFix("focus_cascade", "Show CAS_IN pin"))
        if "has no connections" in message:
            fixes.append(QuickFix("remove_orphan", "Remove orphan block", True))
    return fixes


def _focus_block(scene, block_id: str, terminal_name: str = "") -> bool:
    item = scene._block_items.get(block_id)
    if item is None:
        return False
    scene.clearSelection()
    item.setSelected(True)
    if scene.views():
        scene.views()[0].centerOn(item)
    if terminal_name:
        terminal = (item.get_terminal_item("in", terminal_name)
                    or item.get_terminal_item("out", terminal_name))
        if terminal is not None:
            terminal.set_highlighted(True, True)
            terminal.set_wiring_feedback("Required connection")
            QTimer.singleShot(
                2400,
                lambda t=terminal: (
                    t.set_highlighted(False), t.set_wiring_feedback()
                ) if t.scene() is not None else None,
            )
    return True


def apply_quick_fix(scene, finding: ValidationResult, fix: QuickFix) -> bool:
    """Apply one fix through scene APIs so it participates in undo/redo."""
    block = scene.graph.blocks.get(finding.block_id)
    if block is None:
        return False
    if fix.key == "configure_block":
        return _focus_block(scene, block.id)
    if fix.key == "focus_input":
        return _focus_block(scene, block.id, "IN")
    if fix.key == "focus_cascade":
        return _focus_block(scene, block.id, "CAS_IN")
    if fix.key == "remove_orphan":
        scene.delete_block(block.id)
        return block.id not in scene.graph.blocks
    if fix.key == "normalize_pid_limits":
        from ..undo import ChangeConfigCommand

        old = dict(block.config.params)
        try:
            low = float(old.get("out_lo", 0.0))
            high = float(old.get("out_hi", 100.0))
        except (TypeError, ValueError):
            low, high = 0.0, 100.0
        if low >= high:
            low, high = min(low, high), max(low, high)
            if low == high:
                high = low + 1.0
        new = dict(old)
        new.update(out_lo=low, out_hi=high)
        scene.undo_stack.push(ChangeConfigCommand(
            scene, block.id, old, new, "Normalize PID Output Limits"))
        return True
    if fix.key == "add_bkcal":
        cascade = next((
            wire for wire in scene.graph.wires.values()
            if wire.dst_block_id == block.id
            and wire.dst_terminal == "CAS_IN"
            and scene.graph.blocks.get(wire.src_block_id) is not None
            and scene.graph.blocks[wire.src_block_id].block_type == "PID"
        ), None)
        if cascade is None:
            return False
        return scene.add_wire(
            block.id,
            "BKCAL_OUT",
            cascade.src_block_id,
            "BKCAL_IN",
            is_bkcal=True,
        ) is not None
    return False


class ProblemsPanel(QWidget):
    """Module validation that stays current as the graph changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = None
        self._findings: list[ValidationResult] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.summary = QLabel("No active module")
        row.addWidget(self.summary, 1)
        self.fix_button = QPushButton("Quick Fix")
        self.fix_button.setEnabled(False)
        self.fix_button.clicked.connect(self._show_fixes)
        row.addWidget(self.fix_button)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Severity", "Block", "Problem"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._navigate_item)
        layout.addWidget(self.tree, 1)
        self.setStyleSheet(
            f"QTreeWidget {{ border: 1px solid {UI.border}; }}"
            f"QPushButton {{ padding: 3px 7px; border: 1px solid {UI.border}; }}"
        )

    def set_scene(self, scene) -> None:
        self._scene = scene
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        self._findings = [] if self._scene is None else validate_strategy(
            self._scene.graph)
        colors = {
            "ERROR": QColor("#C62828"),
            "WARNING": QColor("#B56A00"),
            "INFO": QColor("#2868A8"),
        }
        for index, finding in enumerate(self._findings):
            block = self._scene.graph.blocks.get(finding.block_id)
            item = QTreeWidgetItem([
                finding.severity,
                block.instance_name if block else "Module",
                finding.message,
            ])
            item.setData(0, Qt.UserRole, index)
            item.setForeground(0, colors.get(finding.severity, QColor("#333333")))
            self.tree.addTopLevelItem(item)
        errors = sum(f.severity == "ERROR" for f in self._findings)
        warnings = sum(f.severity == "WARNING" for f in self._findings)
        self.summary.setText(
            f"{errors} errors · {warnings} warnings · "
            f"{len(self._findings) - errors - warnings} information"
        )
        self._selection_changed()

    def _current_finding(self) -> ValidationResult | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        index = item.data(0, Qt.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self._findings):
            return None
        return self._findings[index]

    def _selection_changed(self):
        finding = self._current_finding()
        fixes = (quick_fixes_for(self._scene.graph, finding)
                 if self._scene is not None and finding is not None else [])
        self.fix_button.setEnabled(bool(fixes))

    def _navigate_item(self, item, _column):
        self.tree.setCurrentItem(item)
        finding = self._current_finding()
        if finding is not None and finding.block_id and self._scene is not None:
            _focus_block(self._scene, finding.block_id)

    def _show_fixes(self):
        finding = self._current_finding()
        if finding is None or self._scene is None:
            return
        fixes = quick_fixes_for(self._scene.graph, finding)
        if not fixes:
            return
        menu = retain_menu(self, studio_menu(self))
        for fix in fixes:
            action = menu.addAction(fix.label)
            action.setProperty("destructive", fix.destructive)
            action.triggered.connect(
                lambda _checked=False, chosen=fix: self._apply(chosen))
        menu.popup(self.fix_button.mapToGlobal(self.fix_button.rect().bottomLeft()))

    def _apply(self, fix: QuickFix):
        finding = self._current_finding()
        if finding is None or self._scene is None:
            return
        apply_quick_fix(self._scene, finding, fix)
        self.refresh()
