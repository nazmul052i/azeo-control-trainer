"""Modeless validation results for Graphics Designer.

Validation used to end in a message box: it interrupted authoring, could not
be sorted, and gave the engineer no route back to the offending object.  A
commercial editor treats diagnostics as workspace state, so this pane stays
available until the next verification and activates an object on double-click.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF


_SEVERITY_COLOR = {
    "error": Qt.red,
    "warning": Qt.darkYellow,
    "informational": Qt.darkBlue,
}


class ProblemsPane(QTreeWidget):
    """Sortable validation results with a stable raw-problem payload."""

    problem_activated = Signal(str)
    fix_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["Severity", "Display", "Object / path",
                              "Problem"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setColumnWidth(0, 74)
        self.setColumnWidth(1, 150)
        self.setColumnWidth(2, 260)
        self.setStyleSheet(
            f"background: {WF['pane']}; border: 0; font-size: 8.25pt;")
        self.itemDoubleClicked.connect(
            lambda item, _column: self.problem_activated.emit(
                str(item.data(0, Qt.UserRole) or "")))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    def _context_menu(self, position):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        row = self.itemAt(position)
        if row is None:
            return
        menu = QMenu(self)
        locate = menu.addAction("Locate object")
        finding = row.data(0, Qt.UserRole + 1)
        fix = menu.addAction(finding.fix) if getattr(finding, "fix", "") else None
        answer = menu.exec(self.viewport().mapToGlobal(position))
        if answer == locate:
            self.problem_activated.emit(str(row.data(0, Qt.UserRole) or ""))
        elif fix is not None and answer == fix:
            self.fix_requested.emit(finding)
        menu.deleteLater()

    @staticmethod
    def _object_path(problem: str) -> str:
        """Extract the useful navigation token without hiding the message."""
        text = str(problem or "").strip()
        if ": " in text:
            candidate = text.rsplit(": ", 1)[-1].strip()
            if "/" in candidate:
                return candidate
        if "/" in text and " " not in text:
            return text
        if text.startswith("no PVM class registered for "):
            return text.removeprefix("no PVM class registered for ")
        return ""

    def set_problems(self, display_name: str, problems) -> None:
        current = self.currentItem()
        key = tuple(current.text(c) for c in range(4)) if current else None
        scroll = self.verticalScrollBar().value()
        self.setSortingEnabled(False)
        self.clear()
        for problem in problems:
            text = str(getattr(problem, "message", problem))
            severity = str(getattr(problem, "severity", "error")).lower()
            object_path = str(getattr(problem, "item", "") or "") \
                or self._object_path(text)
            row = QTreeWidgetItem([
                severity.upper(), display_name, object_path, text])
            # Activation needs the structured item identity.  Emitting only
            # the prose made findings such as ``unconnected pipe: p1`` show
            # p1 in the grid but navigate with an empty token.
            row.setData(0, Qt.UserRole, object_path or text)
            row.setData(0, Qt.UserRole + 1, problem)
            if getattr(problem, "fix", ""):
                row.setToolTip(3, f"Right-click → {problem.fix} (one Undo step)")
            row.setForeground(0, _SEVERITY_COLOR.get(severity, Qt.red))
            self.addTopLevelItem(row)
            if tuple(row.text(c) for c in range(4)) == key:
                self.setCurrentItem(row)
        self.setSortingEnabled(True)
        self.verticalScrollBar().setValue(scroll)
