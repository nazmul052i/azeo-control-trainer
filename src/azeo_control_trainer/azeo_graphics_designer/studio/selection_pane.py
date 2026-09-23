"""The Selection pane — every element on the display, in z-order.

`DLCreatingOperatorDisplay.pdf` names this pane in eight of its element
operations ("Editing canvas **or Selection pane**"), and it is the one
place several of them are practical at all:

- **hiding an element** — once hidden, it cannot be clicked on the
  canvas, so the only way back is a list that still shows it;
- **locking** — same problem, one step worse: a locked element ignores
  the mouse entirely;
- **reaching what is underneath** — an element completely covered by
  another has no canvas hit area of its own.

**Listed top of z-order first**, because that is the order the operator
sees them stacked and the order that decides which one an overlapping
interaction region belongs to. A pane sorted by name would be easier to
read and would answer a different question.

Two toggles per row, and they are *state*, not a filter on this pane:
hiding an element hides it on the display and in the published
revision. That is why the eye and the padlock are drawn as the element's
own properties rather than as view options — a pane that quietly changed
what the operator sees would be the worst kind of authoring tool.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import retain_menu

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF

#: Column layout. The toggles sit LEFT of the name, where the eye can
#: run down them as a column — the same reason a file browser puts its
#: checkboxes in a gutter rather than after the filename.
COL_VISIBLE, COL_LOCKED, COL_NAME = 0, 1, 2

EYE_ON, EYE_OFF = "◉", "○"
LOCK_ON, LOCK_OFF = "■", "□"


class SelectionPane(QTreeWidget):
    """The display's elements, with visibility and lock."""

    selection_changed = Signal(list)

    def __init__(self, studio, parent: QWidget | None = None):
        super().__init__(parent)
        self.studio = studio
        self.setColumnCount(3)
        self.setHeaderLabels(["", "", "Element"])
        self.setColumnWidth(COL_VISIBLE, 22)
        self.setColumnWidth(COL_LOCKED, 22)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setStyleSheet(
            "background: %s; border: 1px solid %s; font-size: 8.25pt;"
            % (WF["pane"], WF["bd_lt"]))
        self.itemClicked.connect(self._clicked)
        self.itemSelectionChanged.connect(self._selection_changed)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._syncing = False
        self.reload()

    # ------------------------------------------------------- building
    def rows(self) -> list:
        """(item, label) for everything on the display, topmost first.

        PVMs and drawing items together: the pane is about the display,
        and an engineer hunting a buried object does not care which of
        the two families it came from.
        """
        scene = self.studio.canvas.scene()
        entries = []
        for item in scene.items():
            pvm = getattr(item, "pvm", None)
            data = getattr(item, "data", None)
            if pvm is not None:
                path = " · ".join(str(v) for v in (pvm.params or {}).values())
                entries.append((item, "%s  %s" % (pvm.block_type, path)))
            elif isinstance(data, dict) and data.get("kind"):
                label = (data.get("tag") or data.get("text")
                         or data.get("symbol") or data.get("kind"))
                entries.append((item, "%s  %s" % (data.get("kind"), label)))
        # `scene.items()` already returns topmost first; keep it.
        return entries

    def reload(self) -> None:
        self._syncing = True
        self.clear()
        for item, label in self.rows():
            row = QTreeWidgetItem(self)
            row.setText(COL_NAME, label)
            row.setData(COL_NAME, Qt.UserRole, item)
            self._paint_toggles(row, item)
            row.setSelected(item.isSelected())
        self._syncing = False

    @staticmethod
    def _state(item) -> tuple:
        """(visible, locked) for either family of item."""
        data = getattr(item, "data", None)
        if isinstance(data, dict):
            return (bool(data.get("visible", True)),
                    bool(data.get("locked", False)))
        # A PVM carries its own flags rather than a data dict.
        return (bool(getattr(item, "pvm_visible", True)),
                bool(getattr(item, "pvm_locked", False)))

    def _paint_toggles(self, row, item) -> None:
        visible, locked = self._state(item)
        row.setText(COL_VISIBLE, EYE_ON if visible else EYE_OFF)
        row.setText(COL_LOCKED, LOCK_ON if locked else LOCK_OFF)
        # A hidden element is dimmed in the list too. It is still
        # listed — that is the point of the pane — but it should not
        # read as present on the display.
        from PySide6.QtGui import QColor
        row.setForeground(COL_NAME, QColor(
            WF["tx"] if visible else WF["tx2"]))

    # -------------------------------------------------------- editing
    def _clicked(self, row, column: int) -> None:
        item = row.data(COL_NAME, Qt.UserRole)
        if item is None or column not in (COL_VISIBLE, COL_LOCKED):
            return
        visible, locked = self._state(item)
        if column == COL_VISIBLE:
            self.set_visible(item, not visible)
        else:
            self.set_locked(item, not locked)
        self._paint_toggles(row, item)

    def set_visible(self, item, visible: bool) -> None:
        """Hide or show one element, on the display itself."""
        data = getattr(item, "data", None)
        if isinstance(data, dict):
            data["visible"] = bool(visible)
        else:
            item.pvm_visible = bool(visible)
            item.pvm = replace(item.pvm, visible=bool(visible))
        item.setVisible(bool(visible))
        self.studio.mark_unsaved()
        item.update()

    def set_locked(self, item, locked: bool) -> None:
        """Lock or unlock one element.

        A locked element keeps its selectability — you can still reach
        it, inspect it and unlock it — and loses only its movability.
        Locking something into unreachability is how an engineer ends
        up rebuilding a display to fix one object.
        """
        from PySide6.QtWidgets import QGraphicsItem

        data = getattr(item, "data", None)
        if isinstance(data, dict):
            data["locked"] = bool(locked)
        else:
            item.pvm_locked = bool(locked)
            item.pvm = replace(item.pvm, locked=bool(locked))
        item.setFlag(QGraphicsItem.ItemIsMovable, not locked)
        self.studio.mark_unsaved()

    # ------------------------------------------------ shortcut menu
    def _context_menu(self, pos) -> None:
        """Reach the element's real menu from its z-order row.

        Hidden, locked and completely covered objects cannot reliably open a
        canvas menu. The Selection pane is their recovery route, so it must
        expose the same command implementation rather than a smaller parallel
        menu that gradually drifts from the canvas.
        """
        row = self.itemAt(pos)
        if row is not None:
            if not row.isSelected():
                self.clearSelection()
                row.setSelected(True)
                self._selection_changed()
            item = row.data(COL_NAME, Qt.UserRole)
            if item is None:
                return
            global_pos = self.viewport().mapToGlobal(pos)
            if getattr(item, "pvm", None) is not None:
                self.studio.pvm_context_menu(item, global_pos)
            elif isinstance(getattr(item, "data", None), dict):
                self.studio.drawing_context_menu(item, global_pos)
            return

        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu

        menu = studio_menu("SELECTION", "Display elements in z-order")
        select_all = menu.addAction("Select All Elements")
        select_all.setEnabled(self.topLevelItemCount() > 0)
        select_all.triggered.connect(self.selectAll)
        menu.addSeparator()
        show_all = menu.addAction("Show All Elements")
        unlock_all = menu.addAction("Unlock All Elements")
        show_all.setEnabled(any(
            not self._state(item)[0] for item, _label in self.rows()))
        unlock_all.setEnabled(any(
            self._state(item)[1] for item, _label in self.rows()))
        show_all.triggered.connect(lambda: self._set_all("visible", True))
        unlock_all.triggered.connect(lambda: self._set_all("locked", False))
        menu.addSeparator()
        menu.addAction("Refresh").triggered.connect(self.reload)
        retain_menu(self, menu, "_context_menu_instance")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(self.viewport().mapToGlobal(pos))

    def _set_all(self, field: str, value: bool) -> None:
        """Apply a visibility/lock recovery command to every row."""
        for item, _label in self.rows():
            if field == "visible" and self._state(item)[0] != value:
                self.set_visible(item, value)
            elif field == "locked" and self._state(item)[1] != value:
                self.set_locked(item, value)
        self.reload()

    # ------------------------------------------------------ selection
    def _selection_changed(self) -> None:
        if self._syncing:
            return
        chosen = [row.data(COL_NAME, Qt.UserRole)
                  for row in self.selectedItems()]
        current = self.currentItem()
        primary = current.data(COL_NAME, Qt.UserRole) \
            if current is not None else None
        self.studio.selection.replace(
            [item for item in chosen if item is not None], primary=primary)
        self.selection_changed.emit([i for i in chosen if i is not None])

    def sync_from_canvas(self) -> None:
        """Follow a selection made on the canvas.

        Guarded by `_syncing` so the two do not drive each other in a
        loop — the pane sets scene selection and the scene's change
        would otherwise come straight back here.
        """
        self._syncing = True
        snapshot = self.studio.selection.snapshot()
        for index in range(self.topLevelItemCount()):
            row = self.topLevelItem(index)
            item = row.data(COL_NAME, Qt.UserRole)
            row.setSelected(item is not None and item.isSelected())
            if item is snapshot.primary:
                self.setCurrentItem(row)
        self._syncing = False
