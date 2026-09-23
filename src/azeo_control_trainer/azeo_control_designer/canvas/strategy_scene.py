"""Strategy designer scene — manages blocks, wires, and interaction.

Supports:
- Adding/removing blocks and wires
- Wire drawing (click-drag from terminal to terminal)
- Block selection and multi-select
- Copy/paste with undo/redo
- Grid snapping
"""
from __future__ import annotations

import logging
import re
import uuid

import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QBrush, QPainter, QPen, QTransform, QUndoCommand, QUndoStack,
)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsDropShadowEffect

from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import TerminalDirection
from azeo_control_trainer.core.strategy.model.block_base import FunctionBlock
from azeo_control_trainer.core.strategy.model.block_registry import registry
from ..items.block_item import BlockItem
from ..items.comment_item import CommentItem
from ..items.wire_item import WireItem, TempWireItem
from ..items.terminal_item import TerminalItem
from ..undo import (
    AddBlockCommand, DeleteBlockCommand, MoveBlockCommand,
    AddWireCommand, DeleteWireCommand,
    AddCommentCommand, DeleteCommentCommand, EditCommentCommand,
    ReplaceBlockCommand, ToggleBkcalCommand,
)

log = logging.getLogger("strategy.scene")

GRID_SIZE = 20

# A control module is an engineering sheet, not an infinite whiteboard.  The
# previous 6000 x 6000 undifferentiated scene made a small strategy look lost
# and allowed accidental drags thousands of pixels away.  This 4:3 work area
# contains every shipped module (including its tallest block) with useful
# room for expansion.  A narrow surround remains available for page-edge
# context and scroll-bar operation, but authored objects are confined to the
# sheet itself.
SHEET_RECT = QRectF(-1200, -600, 3200, 2400)
SHEET_SURROUND = 160


# ── Composite group / ungroup undo command ──────────────────────────────
# Grouping and ungrouping are structural edits like any other, so they must
# sit on the undo stack.  They are expressed as a swap between two snapshots
# of the affected slice of the graph rather than by re-running
# collapse_to_composite()/explode_composite(), because those functions mint
# fresh ids every time — replaying them would not restore the *same* blocks
# and wires, and the stale composite would be left behind.
#
# A snapshot is (blocks, wires):
#   blocks: [(FunctionBlock, x, y), ...]      — the live block objects
#   wires:  [(wire_id, src_block_id, src_terminal,
#             dst_block_id, dst_terminal, is_bkcal), ...]
#           every wire touching those blocks, so external connections to
#           untouched blocks are restored with their original ids too.
class CompositeStructureCommand(QUndoCommand):
    """Undo/redo for group-into-composite and ungroup (explode)."""

    def __init__(self, scene, before, after, description: str):
        super().__init__(description)
        self._scene = scene
        self._before = before
        self._after = after
        self._first_redo = True  # the operation already ran before the push

    def undo(self):
        self._apply(remove=self._after, add=self._before)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._apply(remove=self._before, add=self._after)

    def _apply(self, remove, add):
        for block, _x, _y in remove[0]:
            self._scene._delete_block_internal(block.id)
        for block, x, y in add[0]:
            # Terminal connect flags are rebuilt from the restored wires by
            # _add_block_internal / _add_wire_internal.
            block.x, block.y = x, y
            self._scene._add_block_internal(block, None)
        for wid, src_bid, src_t, dst_bid, dst_t, is_bkcal in add[1]:
            self._scene._add_wire_internal(
                src_bid, src_t, dst_bid, dst_t, is_bkcal, wire_id=wid)


class StrategyScene(QGraphicsScene):
    """QGraphicsScene for the strategy designer canvas."""

    # Signals
    blockAdded = Signal(str)            # block_id
    blockRemoved = Signal(str)          # block_id
    wireAdded = Signal(str)             # wire_id
    wireRemoved = Signal(str)           # wire_id
    selectionUpdated = Signal(str)      # block_id or ""
    strategyModified = Signal()         # any change
    compileRequested = Signal()
    saveTemplateRequested = Signal()    # save selected blocks as template
    compositeDrillDownRequested = Signal(str)  # composite block_id — open in a new tab
    structureEditBlocked = Signal(str)  # description of the refused edit
    connectionRefused = Signal(str)     # why a connection could not be made
    sceneNotice = Signal(str)           # an action partly/wholly did nothing —
                                        # show it, never swallow it

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sheet_rect = QRectF(SHEET_RECT)
        self.setSceneRect(self._sheet_rect.adjusted(
            -SHEET_SURROUND, -SHEET_SURROUND,
            SHEET_SURROUND, SHEET_SURROUND,
        ))
        # The darker surround makes the finite module sheet legible at every
        # zoom level; the sheet itself is painted in drawBackground().
        self.setBackgroundBrush(QBrush(QColor("#CDD3DB")))

        self._graph = StrategyGraph("Untitled Strategy")
        self._block_items: dict[str, BlockItem] = {}   # block_id -> BlockItem
        self._wire_items: dict[str, WireItem] = {}     # wire_id -> WireItem
        self._comment_items: list[CommentItem] = []    # comment annotations
        self._deferred_comments: list[dict] | None = None

        # Structural edits are locked while the module is online. The compiler
        # holds a reference to this same graph and the runtime scans it from
        # another thread, so adding/removing a block or wire mid-scan can
        # abort the scan with valves half-written, and the compiled execution
        # order would be stale anyway (a deleted wire keeps driving its
        # output). Azeo likewise requires a module to be off-scan to change
        # its structure; parameter tuning stays available online.
        self._structure_locked = False

        # Wire value display
        self._show_wire_values = False

        # Azeo-style live monitoring on the block faces.  Initialised here
        # rather than being conjured by the first set_live_mode() call, so
        # is_live_mode() does not have to getattr() around a missing attribute.
        self._live_mode = False

        # Block ids currently wearing the search highlight.  Tracked so the
        # per-keystroke clear touches only those blocks instead of every block
        # in the module (each set_search_highlight() call allocates a fresh
        # QGraphicsDropShadowEffect).
        self._search_highlighted: set[str] = set()

        # Why the last _add_wire_internal() gave up, so add_wire() can tell
        # the operator instead of dropping the wire without a word.
        self._last_wire_error: str | None = None

        # Wire drawing state
        self._wiring = False
        self._wire_src_terminal: TerminalItem | None = None
        self._temp_wire: TempWireItem | None = None
        self._wire_highlights: list[TerminalItem] = []
        self._wire_snap_target: TerminalItem | None = None

        # Undo stack
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(200)

        # Grid
        self._show_grid = True

    @property
    def graph(self) -> StrategyGraph:
        return self._graph

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    # ------------------------------------------------------- lifetime guard
    def is_alive(self) -> bool:
        """True while the underlying C++ QGraphicsScene still exists.

        The canvas is destroyed when its tab closes or the window tears down,
        but the periodic callers that poll it (the live-value timer, the
        minimap, search) are owned by longer-lived objects and can fire one
        more time afterwards.  Touching any Qt method then raises
        ``RuntimeError: Internal C++ object (StrategyScene) already deleted``.
        The refresh entry points below check this and become no-ops instead.
        """
        return shiboken6.isValid(self)

    def notify_modified(self):
        """Emit :attr:`strategyModified`, tolerating a torn-down scene.

        Undo commands can outlive the C++ scene (a stack trimmed at teardown),
        so they route their "the strategy changed" notification through here.
        """
        if not self.is_alive():
            return
        self.strategyModified.emit()

    def _notice(self, message: str):
        """Report an action that did nothing, or only part of what was asked."""
        log.warning("%s", message)
        if self.is_alive():
            self.sceneNotice.emit(message)

    # ---------------------------------------------------------------- Grid
    @property
    def sheet_rect(self) -> QRectF:
        """Finite rectangle in which module logic may be authored."""
        return QRectF(self._sheet_rect)

    def constrain_item_position(self, item, pos: QPointF) -> QPointF:
        """Keep an item's complete painted bounds inside the module sheet."""
        bounds = item.boundingRect()
        left = self._sheet_rect.left() - bounds.left()
        right = self._sheet_rect.right() - bounds.right()
        top = self._sheet_rect.top() - bounds.top()
        bottom = self._sheet_rect.bottom() - bounds.bottom()

        if right < left:  # Defensive: an item wider than the entire sheet.
            x = self._sheet_rect.center().x() - bounds.center().x()
        else:
            x = min(max(pos.x(), left), right)
        if bottom < top:
            y = self._sheet_rect.center().y() - bounds.center().y()
        else:
            y = min(max(pos.y(), top), bottom)
        return QPointF(x, y)

    def constrain_scene_point(self, pos: QPointF) -> QPointF:
        """Clamp a route/control point to the visible engineering sheet."""
        return QPointF(
            min(max(pos.x(), self._sheet_rect.left()),
                self._sheet_rect.right()),
            min(max(pos.y(), self._sheet_rect.top()),
                self._sheet_rect.bottom()),
        )

    def drawBackground(self, painter: QPainter, rect):
        super().drawBackground(painter, rect)

        painter.save()
        painter.setPen(QPen(QColor("#8793A2"), 1))
        painter.setBrush(QBrush(QColor("#E4E7EC")))
        painter.drawRect(self._sheet_rect)

        if not self._show_grid:
            painter.restore()
            return

        # Draw grid dots only on the engineering sheet.  A grid continuing
        # through the surround visually says that the page has no boundary.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 30)))

        grid_rect = rect.intersected(self._sheet_rect)
        left = int(grid_rect.left()) - (int(grid_rect.left()) % GRID_SIZE)
        top = int(grid_rect.top()) - (int(grid_rect.top()) % GRID_SIZE)

        x = left
        while x < grid_rect.right():
            y = top
            while y < grid_rect.bottom():
                if self._sheet_rect.contains(QPointF(x, y)):
                    painter.drawEllipse(QPointF(x, y), 1, 1)
                y += GRID_SIZE
            x += GRID_SIZE
        painter.restore()

    def snap_to_grid(self, pos: QPointF) -> QPointF:
        x = round(pos.x() / GRID_SIZE) * GRID_SIZE
        y = round(pos.y() / GRID_SIZE) * GRID_SIZE
        return QPointF(x, y)

    # ---------------------------------------------------------------- Blocks
    def add_block(self, block: FunctionBlock, pos: QPointF | None = None) -> BlockItem:
        """Add a function block to the scene (with undo support)."""
        if self._refuse_structural_edit("add a block"):
            return None
        if pos:
            pos = self.snap_to_grid(pos)
            block.x = pos.x()
            block.y = pos.y()

        item = self._add_block_internal(block, pos)
        self._undo_stack.push(AddBlockCommand(self, block, pos))
        return item

    def _add_block_internal(self, block: FunctionBlock, pos: QPointF | None = None) -> BlockItem:
        """Add a block without pushing to undo stack."""
        if not self.is_alive():
            # An undo/redo arriving after the canvas was destroyed. Do not
            # half-mutate the graph — there is no canvas left to show it on.
            return None
        if pos:
            snapped = self.snap_to_grid(pos)
            block.x = snapped.x()
            block.y = snapped.y()

        if block.id not in self._graph.blocks:
            # Re-adding a block that was deleted (undo, ungroup, composite
            # restore): its terminals may still carry `connected = True` from
            # wires that no longer exist.  The wires are restored right after
            # this call and set the flag themselves, so start from a clean
            # slate — a stale flag paints phantom connections and makes
            # "input already connected" refuse a legitimate wire.
            for term in list(block.inputs.values()) + list(block.outputs.values()):
                term.connected = False
            self._graph.add_block(block)

        item = BlockItem(block)
        self.addItem(item)
        # BlockItem receives its persisted position before it has a scene, so
        # its itemChange guard cannot constrain that first setPos().  Clamp it
        # once attached; subsequent user drags are constrained by the item.
        item.setPos(self.constrain_item_position(item, item.pos()))
        self._block_items[block.id] = item
        self._attach_block_item(item)

        self.blockAdded.emit(block.id)
        self.strategyModified.emit()
        log.info("Added block: %s at (%.0f, %.0f)", block, block.x, block.y)
        return item

    # ------------------------------------------------- block item lifetime
    def _attach_block_item(self, item: BlockItem):
        """Wire a BlockItem's signals to the scene.

        Single place so every construction path (add, load, paste, group,
        ungroup) connects the same set — they used to be copy-pasted five
        times, with a different lambda signature in two of them.
        """
        item.positionChanged.connect(self._on_block_moved)
        item.blockSelected.connect(self._on_block_selected)
        item.blockDoubleClicked.connect(self._on_block_double_clicked)
        item.blockResized.connect(self._on_block_resized)
        if self._live_mode:
            item.set_live_mode(True)

    def _detach_block_item(self, item: BlockItem | None):
        """Drop a BlockItem's connections before it leaves the scene.

        An item removed with ``removeItem()`` is no longer owned by the scene
        but stays alive as long as anything (an undo command, a properties
        panel, a watch entry) holds the Python wrapper — and it keeps its
        connections into scene slots.  Disconnecting here means a late signal
        from an orphaned item cannot reach a scene that may already be gone.
        """
        if item is None:
            return
        for sig in (item.positionChanged, item.blockSelected,
                    item.blockDoubleClicked, item.blockResized):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                # Nothing connected, or the C++ item is already gone.  Both
                # mean there is no connection left to worry about.
                pass
        self._search_highlighted.discard(item.block.id)

    def _on_block_resized(self, block_id: str):
        """A block was resized on the canvas: reroute its wires and mark the
        module modified (the size is persisted as ui_width/ui_height)."""
        self._on_block_moved(block_id, 0, 0)
        self.notify_modified()

    def add_block_by_type(self, block_type: str, pos: QPointF | None = None) -> BlockItem | None:
        """Create and add a block by type name."""
        block = registry.create(block_type)
        if block is None:
            log.warning("Unknown block type: %s", block_type)
            return None
        return self.add_block(block, pos)

    def quick_insert_from_terminal(
        self,
        source: TerminalItem,
        candidate,
        pos: QPointF,
    ) -> BlockItem | None:
        """Insert one compatible block and connect it as a single undo step."""
        if self._refuse_structural_edit("quick-insert a connected block"):
            return None
        if not shiboken6.isValid(source) or source.scene() is not self:
            self._notice("Quick Insert source no longer exists")
            return None
        block = registry.create(candidate.block_type)
        if block is None:
            self._notice(f"Quick Insert block {candidate.block_type} is unavailable")
            return None
        block.instance_name = self._unique_name(
            block.block_type,
            {existing.instance_name for existing in self._graph.blocks.values()},
        )

        source_block = source.parent_block.block
        if source.terminal.direction == TerminalDirection.OUTPUT:
            wire_args = (
                source_block.id,
                source.terminal.name,
                block.id,
                candidate.terminal_name,
            )
        else:
            wire_args = (
                block.id,
                candidate.terminal_name,
                source_block.id,
                source.terminal.name,
            )

        self._undo_stack.beginMacro(f"Quick Insert {candidate.block_type}")
        try:
            item = self.add_block(block, pos)
            if item is None:
                return None
            terminal = (source.terminal if source.terminal.is_bkcal else
                        (block.inputs.get(candidate.terminal_name)
                         or block.outputs.get(candidate.terminal_name)))
            wire = self.add_wire(
                *wire_args,
                is_bkcal=bool(terminal and terminal.is_bkcal),
            )
            if wire is None:
                # Candidate enumeration and final graph checks deliberately
                # both run. If state changed while the picker was open, keep
                # the inserted block visible and report the refused wire.
                self._notice(
                    f"Inserted {block.instance_name}, but its connection was refused"
                )
            self.clearSelection()
            item.setSelected(True)
            return item
        finally:
            self._undo_stack.endMacro()

    def insert_block_into_wire(self, wire_id: str, candidate,
                               pos: QPointF) -> BlockItem | None:
        """Splice a compatible block into a connection in one undo step."""
        if self._refuse_structural_edit("insert a block into a connection"):
            return None
        wire = self._graph.wires.get(wire_id)
        if wire is None:
            return None
        block = registry.create(candidate.block_type)
        if block is None:
            return None
        block.instance_name = self._unique_name(
            block.block_type,
            {existing.instance_name for existing in self._graph.blocks.values()},
        )
        before = (wire.src_block_id, wire.src_terminal,
                  wire.dst_block_id, wire.dst_terminal, wire.is_bkcal)
        self._undo_stack.beginMacro(f"Insert {candidate.block_type} into Connection")
        try:
            self.delete_wire(wire_id)
            item = self.add_block(block, pos)
            if item is None:
                return None
            first = self.add_wire(
                before[0], before[1], block.id, candidate.input_name,
                is_bkcal=before[4],
            )
            second = self.add_wire(
                block.id, candidate.output_name, before[2], before[3],
                is_bkcal=before[4],
            )
            if first is None or second is None:
                self._notice("The block was inserted, but a replacement wire was refused")
            self.clearSelection()
            item.setSelected(True)
            return item
        finally:
            self._undo_stack.endMacro()

    def open_wire_insert_dialog(self, wire_id: str, pos: QPointF, parent=None):
        from azeo_control_trainer.core.presentation.headless import is_headless
        from ..dialogs.quick_insert import (
            QuickInsertDialog,
            compatible_wire_insert_candidates,
        )

        wire = self._graph.wires.get(wire_id)
        if wire is None:
            return None
        candidates = compatible_wire_insert_candidates(self._graph, wire)
        dialog = QuickInsertDialog(
            parent=parent,
            candidates=candidates,
            prompt=(f"Insert a compatible block between "
                    f"{wire.src_terminal} and {wire.dst_terminal}"),
        )
        dialog.candidateChosen.connect(
            lambda candidate: self.insert_block_into_wire(wire_id, candidate, pos))
        self._wire_insert_dialog = dialog
        if not is_headless():
            dialog.show()
        return dialog

    def reconnect_wire(self, wire_id: str, endpoint: str, target) -> bool:
        """Reconnect one endpoint while retaining the operation as one undo."""
        if self._refuse_structural_edit("reconnect a connection"):
            return False
        wire = self._graph.wires.get(wire_id)
        if wire is None or endpoint not in {"source", "destination"}:
            return False
        src_block = target.block_id if endpoint == "source" else wire.src_block_id
        src_terminal = target.terminal_name if endpoint == "source" else wire.src_terminal
        dst_block = target.block_id if endpoint == "destination" else wire.dst_block_id
        dst_terminal = target.terminal_name if endpoint == "destination" else wire.dst_terminal

        # Validate with the old wire absent because its current destination is
        # legitimately occupied by itself.
        from azeo_control_trainer.core.strategy.model.strategy_graph import types_compatible

        src = self._graph.blocks.get(src_block)
        dst = self._graph.blocks.get(dst_block)
        if (src is None or dst is None or src_terminal not in src.outputs
                or dst_terminal not in dst.inputs):
            return False
        if not types_compatible(src.outputs[src_terminal].data_type,
                                dst.inputs[dst_terminal].data_type):
            return False
        if any(other.id != wire_id
               and other.dst_block_id == dst_block
               and other.dst_terminal == dst_terminal
               for other in self._graph.wires.values()):
            return False

        feedback = wire.is_bkcal
        self._undo_stack.beginMacro(f"Reconnect {endpoint.title()}")
        try:
            self.delete_wire(wire_id)
            return self.add_wire(
                src_block, src_terminal, dst_block, dst_terminal,
                is_bkcal=feedback,
            ) is not None
        finally:
            self._undo_stack.endMacro()

    def open_reconnect_dialog(self, wire_id: str, endpoint: str, parent=None):
        from azeo_control_trainer.core.presentation.headless import is_headless
        from ..dialogs.connection_target import ConnectionTargetDialog, reconnect_targets

        wire = self._graph.wires.get(wire_id)
        if wire is None:
            return None
        dialog = ConnectionTargetDialog(
            reconnect_targets(self._graph, wire, endpoint), endpoint, parent=parent)
        dialog.targetChosen.connect(
            lambda target: self.reconnect_wire(wire_id, endpoint, target))
        self._reconnect_dialog = dialog
        if not is_headless():
            dialog.show()
        return dialog

    def remove_block_and_heal(self, block_id: str) -> bool:
        """Delete a simple pass-through block and join its neighbours."""
        if self._refuse_structural_edit("remove and heal a block"):
            return False
        incoming = [wire for wire in self._graph.wires.values()
                    if wire.dst_block_id == block_id and not wire.is_bkcal]
        outgoing = [wire for wire in self._graph.wires.values()
                    if wire.src_block_id == block_id and not wire.is_bkcal]
        touching = self._graph.get_wires_for_block(block_id)
        if len(incoming) != 1 or len(outgoing) != 1 or len(touching) != 2:
            self._notice("Remove and Heal requires exactly one input and one output")
            return False
        source, destination = incoming[0], outgoing[0]
        from azeo_control_trainer.core.strategy.model.strategy_graph import types_compatible

        source_type = self._graph.blocks[source.src_block_id].outputs[
            source.src_terminal].data_type
        destination_type = self._graph.blocks[destination.dst_block_id].inputs[
            destination.dst_terminal].data_type
        if not types_compatible(source_type, destination_type):
            self._notice("Remove and Heal cannot join incompatible signal types")
            return False
        self._undo_stack.beginMacro("Remove Block and Heal Connection")
        try:
            self.delete_block(block_id)
            return self.add_wire(
                source.src_block_id, source.src_terminal,
                destination.dst_block_id, destination.dst_terminal,
            ) is not None
        finally:
            self._undo_stack.endMacro()

    def assign_tag_io(
        self,
        block_id: str,
        tag_definition: str,
        *,
        config: dict | None = None,
    ) -> FunctionBlock | None:
        """Convert a ``TAGIO`` placeholder to its assigned monitor type.

        The assignment changes executable structure, so it is refused while
        the module is on scan.  Offline it is one undoable replacement that
        retains the block UUID and every connected wire ID.
        """
        if self._refuse_structural_edit("assign a TAGIO control tag"):
            return None

        from azeo_control_trainer.core.strategy.blocks.dv_tag_io_blocks import (
            TagIoBlock,
        )

        previous = self._graph.blocks.get(block_id)
        if not isinstance(previous, TagIoBlock):
            raise TypeError("TAG assignment requires a TAGIO placeholder")
        replacement = previous.convert_assignment(
            tag_definition,
            config=config,
        )
        primary = str(getattr(replacement, "primary_terminal", "OUT"))
        self._undo_stack.push(
            ReplaceBlockCommand(
                self,
                previous,
                replacement,
                output_terminal_map={"OUT": primary},
                description=f"Assign {replacement.block_type} Control Tag",
            )
        )
        return self._graph.blocks.get(block_id)

    def _replace_block_internal(
        self,
        replacement: FunctionBlock,
        *,
        input_terminal_map: dict[str, str] | None = None,
        output_terminal_map: dict[str, str] | None = None,
    ) -> BlockItem | None:
        """Swap a model block and its graphics without dropping its wires.

        Called only by :class:`ReplaceBlockCommand`.  The graph validates the
        complete proposed replacement before any model mutation; the scene
        then rebuilds only the affected block and wire graphics around those
        same model objects.
        """
        if not self.is_alive():
            return None
        block_id = replacement.id
        previous = self._graph.blocks.get(block_id)
        if previous is None:
            raise KeyError(f"block {block_id!r} is no longer in the strategy")
        previous_item = self._block_items.get(block_id)
        was_selected = bool(previous_item and previous_item.isSelected())
        connected = list(self._graph.get_wires_for_block(block_id))

        # The model operation is atomic and can still refuse an incompatible
        # terminal remap.  Do it before removing a single graphics item.
        self._graph.replace_block(
            block_id,
            replacement,
            input_terminal_map=input_terminal_map,
            output_terminal_map=output_terminal_map,
        )

        for wire in connected:
            item = self._wire_items.pop(wire.id, None)
            if item is not None:
                self.removeItem(item)
        if previous_item is not None:
            self._detach_block_item(previous_item)
            self.removeItem(previous_item)

        item = BlockItem(replacement)
        self.addItem(item)
        self._block_items[block_id] = item
        self._attach_block_item(item)

        touched_blocks = {block_id}
        for wire in connected:
            src_item = self._block_items.get(wire.src_block_id)
            dst_item = self._block_items.get(wire.dst_block_id)
            src_terminal = (
                src_item.get_terminal_item("out", wire.src_terminal)
                if src_item is not None else None
            )
            dst_terminal = (
                dst_item.get_terminal_item("in", wire.dst_terminal)
                if dst_item is not None else None
            )
            if src_terminal is None or dst_terminal is None:
                # Model validation already established the terminal exists;
                # reaching this branch means UI pin visibility is corrupt and
                # must be loud rather than leaving an executable invisible
                # connection.
                raise RuntimeError(
                    f"wire {wire.id} could not be redrawn after block "
                    f"replacement"
                )
            wire_item = WireItem(wire, src_terminal, dst_terminal)
            self.addItem(wire_item)
            self._wire_items[wire.id] = wire_item
            touched_blocks.update((wire.src_block_id, wire.dst_block_id))

        for touched_id in touched_blocks:
            touched = self._block_items.get(touched_id)
            if touched is not None:
                touched.refresh_terminals()
        if was_selected:
            item.setSelected(True)
        self.blockAdded.emit(block_id)
        self.selectionUpdated.emit(block_id if was_selected else "")
        self.strategyModified.emit()
        return item

    # ---------------------------------------------------------- edit lock
    def set_structure_locked(self, locked: bool):
        """Lock/unlock structural edits (called when the module goes on/off scan)."""
        self._structure_locked = bool(locked)

    @property
    def structure_locked(self) -> bool:
        return self._structure_locked

    def _refuse_structural_edit(self, what: str) -> bool:
        """Return True (and notify) when a structural edit must be refused."""
        if not self._structure_locked:
            return False
        self.structureEditBlocked.emit(what)
        return True

    def delete_block(self, block_id: str):
        """Delete a block and all connected wires (with undo support)."""
        if self._refuse_structural_edit("delete a block"):
            return
        block = self._graph.blocks.get(block_id)
        if not block:
            return
        connected_wires = list(self._graph.get_wires_for_block(block_id))
        # push() runs redo(), which performs the deletion — deleting again
        # here fired blockRemoved (and strategyModified) twice per delete.
        self._undo_stack.push(
            DeleteBlockCommand(self, block, connected_wires))

    def _delete_block_internal(self, block_id: str):
        """Delete a block and connected wires without pushing to undo stack."""
        if not self.is_alive():
            return
        # Remove connected wires first
        wire_ids = [w.id for w in self._graph.get_wires_for_block(block_id)]
        for wid in wire_ids:
            self._delete_wire_internal(wid)

        # Remove block item
        item = self._block_items.pop(block_id, None)
        if item:
            debugger = getattr(item, "_sfc_debugger_dialog", None)
            if debugger is not None:
                debugger.close()
            self._detach_block_item(item)
            self.removeItem(item)

        self._graph.remove_block(block_id)
        self.blockRemoved.emit(block_id)
        self.strategyModified.emit()

    # ---------------------------------------------------------------- Wires
    def add_wire(self, src_block_id: str, src_terminal: str,
                 dst_block_id: str, dst_terminal: str,
                 is_bkcal: bool = False) -> WireItem | None:
        """Add a wire between two terminals (with undo support).

        A connection the graph refuses (incompatible data types, an input that
        is already wired, a terminal that does not exist) is reported through
        :attr:`connectionRefused` so the designer can tell the operator why —
        it must never fail silently.
        """
        if self._refuse_structural_edit("add a connection"):
            return None
        reason = self._graph.check_connection(
            src_block_id, src_terminal, dst_block_id, dst_terminal)
        if reason:
            log.warning("Connection refused: %s", reason)
            self.connectionRefused.emit(reason)
            return None
        wire_item = self._add_wire_internal(
            src_block_id, src_terminal, dst_block_id, dst_terminal, is_bkcal)
        if wire_item is None:
            self.connectionRefused.emit(self._last_wire_error or (
                "the connection could not be created — see the log for details"))
            return None
        self._undo_stack.push(AddWireCommand(
            self, wire_item.wire.id,
            src_block_id, src_terminal,
            dst_block_id, dst_terminal, is_bkcal))
        return wire_item

    def _add_wire_internal(self, src_block_id: str, src_terminal: str,
                           dst_block_id: str, dst_terminal: str,
                           is_bkcal: bool = False,
                           wire_id: str | None = None,
                           allow_type_mismatch: bool = False,
                           route_points: list | None = None) -> WireItem | None:
        """Add a wire without pushing to undo stack.

        ``allow_type_mismatch`` is for rebuild paths (paste of a copied
        selection) that must reproduce an existing graph verbatim rather than
        silently dropping a legacy wire.
        """
        from azeo_control_trainer.core.strategy.model.wire import Wire

        self._last_wire_error = None
        if not self.is_alive():
            return None

        # add_wire() surfaces _last_wire_error itself on connectionRefused.
        # A *restore* (undo/redo, composite regroup) has no such caller, so a
        # failure there would vanish — report those on sceneNotice.
        restoring = wire_id is not None

        def fail(message: str, rollback_id: str | None = None):
            self._last_wire_error = message
            if rollback_id:
                self._graph.remove_wire(rollback_id)
            if restoring:
                self._notice(f"Connection {src_block_id}.{src_terminal} -> "
                             f"{dst_block_id}.{dst_terminal} could not be "
                             f"restored: {message}")
            else:
                log.warning("_add_wire_internal: %s", message)
            return None

        if wire_id:
            # Re-creating a wire with a specific id (undo/redo)
            src_block = self._graph.blocks.get(src_block_id)
            dst_block = self._graph.blocks.get(dst_block_id)
            if not src_block or not dst_block:
                return fail("one of the connected blocks is no longer "
                            "in the strategy")
            wire = Wire(src_block_id, src_terminal,
                        dst_block_id, dst_terminal, is_bkcal, wire_id=wire_id,
                        route_points=route_points)
            self._graph.wires[wire.id] = wire
            # Mark terminals as connected
            if src_terminal in src_block.outputs:
                src_block.outputs[src_terminal].connected = True
            if dst_terminal in dst_block.inputs:
                dst_block.inputs[dst_terminal].connected = True
            actual_wire_id = wire.id
        else:
            actual_wire_id = self._graph.add_wire(
                src_block_id, src_terminal,
                dst_block_id, dst_terminal, is_bkcal,
                allow_type_mismatch=allow_type_mismatch)
            if actual_wire_id is None:
                # StrategyGraph.add_wire already logged the specific reason;
                # re-derive it so the canvas can show it.
                return fail(self._graph.check_connection(
                    src_block_id, src_terminal,
                    dst_block_id, dst_terminal)
                    or "the connection was refused — see the log for details")

        wire = self._graph.wires[actual_wire_id]
        if route_points is not None:
            wire.route_points = [list(point) for point in route_points]

        # Find terminal items
        src_item = self._block_items.get(src_block_id)
        dst_item = self._block_items.get(dst_block_id)
        # A wire the canvas cannot draw must not stay in the graph: it would
        # execute and drive its output with nothing on screen to show for it.
        if not src_item or not dst_item:
            return fail("one of the connected blocks has no graphic on "
                        "this canvas", rollback_id=actual_wire_id)

        src_ti = src_item.get_terminal_item("out", src_terminal)
        dst_ti = dst_item.get_terminal_item("in", dst_terminal)
        if not src_ti or not dst_ti:
            missing = src_terminal if not src_ti else dst_terminal
            return fail(f"terminal '{missing}' is hidden on this canvas — "
                        f"show it before connecting to it",
                        rollback_id=actual_wire_id)

        wire_item = WireItem(wire, src_ti, dst_ti)
        self.addItem(wire_item)
        wire_item.update_position()
        self._wire_items[actual_wire_id] = wire_item

        # Refresh terminal appearance
        src_item.refresh_terminals()
        dst_item.refresh_terminals()

        self.wireAdded.emit(actual_wire_id)
        self.strategyModified.emit()
        return wire_item

    def delete_wire(self, wire_id: str):
        """Delete a wire (with undo support)."""
        if self._refuse_structural_edit("delete a connection"):
            return
        wire = self._graph.wires.get(wire_id)
        if not wire:
            return
        # As with delete_block: push() → redo() already deletes.
        self._undo_stack.push(DeleteWireCommand(
            self, wire_id,
            wire.src_block_id, wire.src_terminal,
            wire.dst_block_id, wire.dst_terminal,
            wire.is_bkcal,
            route_points=wire.route_points))

    def _delete_wire_internal(self, wire_id: str):
        """Delete a wire without pushing to undo stack."""
        if not self.is_alive():
            return
        wire = self._graph.wires.get(wire_id)
        if not wire:
            return

        item = self._wire_items.pop(wire_id, None)
        if item:
            self.removeItem(item)

        src_item = self._block_items.get(wire.src_block_id)
        dst_item = self._block_items.get(wire.dst_block_id)

        self._graph.remove_wire(wire_id)

        # Refresh terminal appearance
        if src_item:
            src_item.refresh_terminals()
        if dst_item:
            dst_item.refresh_terminals()

        self.wireRemoved.emit(wire_id)
        self.strategyModified.emit()

    def set_wire_bkcal(self, wire_id: str, is_bkcal: bool):
        """Flip a wire's BKCAL flag (with undo support).

        Call this instead of assigning ``wire.is_bkcal`` directly — that
        changes how the strategy compiles (forward vs back-calculation path)
        with nothing on the undo stack and no dirty marker.
        """
        wire = self._graph.wires.get(wire_id)
        if wire is None or bool(wire.is_bkcal) == bool(is_bkcal):
            return
        if self._refuse_structural_edit("change a connection's BKCAL flag"):
            return
        self._undo_stack.push(
            ToggleBkcalCommand(self, wire_id, wire.is_bkcal, bool(is_bkcal)))

    def _set_wire_bkcal_internal(self, wire_id: str, is_bkcal: bool):
        wire = self._graph.wires.get(wire_id)
        if wire is None:
            log.warning("ToggleBkcalCommand: wire %s is no longer in the "
                        "graph — the BKCAL flag could not be applied", wire_id)
            return
        wire.is_bkcal = bool(is_bkcal)
        item = self._wire_items.get(wire_id)
        if item is not None and shiboken6.isValid(item):
            # BKCAL wires are drawn dashed amber, signal wires solid in their
            # data-type colour.  WireItem owns that styling, so it gets one
            # documented hook: refresh_bkcal_style().  Until it grows one the
            # wire still re-routes and repaints — only the colour lags.
            restyle = getattr(item, "refresh_bkcal_style", None)
            if callable(restyle):
                restyle()
            item.update_position()
            item.update()
        self.notify_modified()

    def update_all_wires(self):
        """Recalculate all wire paths (after block moves)."""
        if not self.is_alive():
            return
        for wire_item in self._wire_items.values():
            wire_item.update_position()

    # ---------------------------------------------------------------- Wire drawing
    def _on_block_moved(self, block_id: str, x: float, y: float):
        """Update wires when a block is moved."""
        for wire in self._graph.get_wires_for_block(block_id):
            wire_item = self._wire_items.get(wire.id)
            if wire_item:
                wire_item.update_position()

    def _connection_orientation(
        self,
        first: TerminalItem,
        second: TerminalItem,
    ) -> tuple[TerminalItem, TerminalItem] | None:
        """Return the output/input pair represented by two graphics pins."""
        if first.terminal.direction == second.terminal.direction:
            return None
        if first.terminal.direction == TerminalDirection.OUTPUT:
            return first, second
        return second, first

    def _connection_reason(
        self,
        first: TerminalItem,
        second: TerminalItem,
    ) -> str | None:
        oriented = self._connection_orientation(first, second)
        if oriented is None:
            return "Connections require one output and one input"
        src, dst = oriented
        return self._graph.check_connection(
            src.parent_block.block.id,
            src.terminal.name,
            dst.parent_block.block.id,
            dst.terminal.name,
        )

    def _clear_wiring_feedback(self):
        for terminal in self._wire_highlights:
            if shiboken6.isValid(terminal):
                terminal.set_highlighted(False)
                terminal.set_wiring_feedback()
        self._wire_highlights.clear()
        self._wire_snap_target = None

    def start_wiring(self, terminal_item: TerminalItem) -> bool:
        """Begin wire drawing from a terminal."""
        if self._refuse_structural_edit("add a connection"):
            return False
        # Replacing an existing input is a deliberate reconnect operation;
        # an ordinary drag must not silently detach the live source.
        if (terminal_item.terminal.direction == TerminalDirection.INPUT
                and terminal_item.terminal.connected):
            self.connectionRefused.emit(
                f"input {terminal_item.parent_block.block.instance_name}."
                f"{terminal_item.terminal.name} is already connected")
            return False

        self._clear_wiring_feedback()
        self._wiring = True
        self._wire_src_terminal = terminal_item

        for block_item in self._block_items.values():
            for candidate in block_item.terminal_items.values():
                if candidate is terminal_item:
                    continue
                # Same-direction pins are not viable destinations and showing
                # every one in red obscures the useful compatible targets.
                if candidate.terminal.direction == terminal_item.terminal.direction:
                    continue
                reason = self._connection_reason(terminal_item, candidate)
                candidate.set_highlighted(True, compatible=reason is None)
                if reason is None:
                    candidate.set_wiring_feedback(
                        f"Compatible {candidate.terminal.data_type.value} connection")
                else:
                    candidate.set_wiring_feedback(f"Cannot connect: {reason}")
                self._wire_highlights.append(candidate)

        # Create temporary orthogonal wire
        self._temp_wire = TempWireItem(terminal_item)
        self.addItem(self._temp_wire)
        return True

    def update_wiring(self, scene_pos: QPointF):
        """Update temporary wire endpoint during drag."""
        if self._temp_wire and self._wire_src_terminal:
            # A pin remains easy to acquire regardless of its small visual
            # size.  Only legal targets are magnetic; an incompatible pin is
            # still red and explains itself in its tooltip.
            nearest = None
            nearest_distance = 18.0 ** 2
            for candidate in self._wire_highlights:
                if self._connection_reason(self._wire_src_terminal, candidate):
                    continue
                center = candidate.get_scene_center()
                distance = ((center.x() - scene_pos.x()) ** 2
                            + (center.y() - scene_pos.y()) ** 2)
                if distance <= nearest_distance:
                    nearest = candidate
                    nearest_distance = distance
            self._wire_snap_target = nearest
            endpoint = nearest.get_scene_center() if nearest else scene_pos
            self._temp_wire.set_end_point(endpoint)

    @property
    def wiring_source(self) -> TerminalItem | None:
        return self._wire_src_terminal

    @property
    def wiring_snap_target(self) -> TerminalItem | None:
        return self._wire_snap_target

    def finish_wiring(self, target_terminal: TerminalItem | None):
        """Complete or cancel wire drawing."""
        # Remove temp wire
        if self._temp_wire:
            self.removeItem(self._temp_wire)
            self._temp_wire = None

        target_terminal = target_terminal or self._wire_snap_target
        if target_terminal and self._wire_src_terminal:
            src = self._wire_src_terminal
            dst = target_terminal

            # Ensure we connect output -> input (swap if needed)
            if (src.terminal.direction == TerminalDirection.INPUT and
                dst.terminal.direction == TerminalDirection.OUTPUT):
                src, dst = dst, src

            if (src.terminal.direction == TerminalDirection.OUTPUT and
                dst.terminal.direction == TerminalDirection.INPUT):

                src_block = src.parent_block
                dst_block = dst.parent_block
                if hasattr(src_block, 'block') and hasattr(dst_block, 'block'):
                    # Auto-detect BKCAL
                    is_bkcal = src.terminal.is_bkcal or dst.terminal.is_bkcal
                    self.add_wire(
                        src_block.block.id, src.terminal.name,
                        dst_block.block.id, dst.terminal.name,
                        is_bkcal=is_bkcal,
                    )

        self._wiring = False
        self._wire_src_terminal = None
        self._clear_wiring_feedback()

    # ---------------------------------------------------------------- Comments
    # Comments are annotations, not logic, so they are allowed while the
    # module is on scan — but they *are* saved with the module, so they must
    # be undoable and must mark the strategy modified.  Deleting one used to
    # be unrecoverable: the text went straight to the bin with nothing on the
    # undo stack.
    def add_comment(self, text: str = "Comment",
                    pos: QPointF | None = None) -> CommentItem:
        """Add a comment annotation to the canvas (with undo support)."""
        x = pos.x() if pos else 0
        y = pos.y() if pos else 0
        item = CommentItem(text=text, x=x, y=y)
        self._add_comment_internal(item)
        self._undo_stack.push(AddCommentCommand(self, item))
        return item

    def delete_comment(self, item: CommentItem):
        """Remove a comment from the canvas (with undo support)."""
        if item not in self._comment_items:
            return
        # push() → redo() performs the removal.
        self._undo_stack.push(DeleteCommentCommand(self, item))

    def set_comment_text(self, item: CommentItem, text: str):
        """Change a comment's text (with undo support).

        ``CommentItem._edit_text`` currently mutates the item directly, so an
        edited comment neither dirties the module nor lands on the undo stack;
        routing that dialog through here fixes both.
        """
        old = getattr(item, "text", "")
        if old == text:
            return
        self._undo_stack.push(EditCommentCommand(self, item, old, text))

    def _add_comment_internal(self, item: CommentItem):
        """Put a comment item on the canvas without touching the undo stack."""
        if not self.is_alive() or not shiboken6.isValid(item):
            return
        self.addItem(item)
        item.setPos(self.constrain_item_position(item, item.pos()))
        if item not in self._comment_items:
            self._comment_items.append(item)
        self._attach_comment_item(item)
        self.notify_modified()

    def _remove_comment_internal(self, item: CommentItem):
        """Take a comment item off the canvas without touching the undo stack.

        The item stays alive in the undo command that owns it, so undo puts
        back the very same note — text, size and position intact.
        """
        if item in self._comment_items:
            self._comment_items.remove(item)
        if not self.is_alive() or not shiboken6.isValid(item):
            return
        if getattr(item, "_scene_attached", False):
            try:
                item.positionChanged.disconnect(self.notify_modified)
            except (RuntimeError, TypeError):
                pass
            item._scene_attached = False
        self.removeItem(item)
        self.notify_modified()

    def _set_comment_text_internal(self, item: CommentItem, text: str):
        if not shiboken6.isValid(item):
            return
        item.text = text
        self.notify_modified()

    def delete_selected(self, description: str = "Delete selection") -> int:
        """Delete the complete logical selection as one undoable command."""
        selected = list(self.selectedItems())
        block_ids = [item.block.id for item in selected
                     if isinstance(item, BlockItem)]
        wire_ids = [item.wire.id for item in selected
                    if isinstance(item, WireItem)]
        comments = [item for item in selected
                    if isinstance(item, CommentItem)]
        if not (block_ids or wire_ids or comments):
            return 0
        if (block_ids or wire_ids) and self._refuse_structural_edit(
                "delete selection"):
            return 0

        self._undo_stack.beginMacro(description)
        try:
            # Blocks go first because their delete command owns attached
            # wires; an explicitly selected attached wire then becomes a
            # harmless no-op rather than a second command.
            for block_id in block_ids:
                self.delete_block(block_id)
            for wire_id in wire_ids:
                self.delete_wire(wire_id)
            for comment in comments:
                self.delete_comment(comment)
        finally:
            self._undo_stack.endMacro()
        return len(block_ids) + len(wire_ids) + len(comments)

    def _attach_comment_item(self, item: CommentItem):
        """Moving or resizing a comment changes what gets saved, so it has to
        mark the module modified like any other edit."""
        if getattr(item, "_scene_attached", False):
            return
        item.positionChanged.connect(self.notify_modified)
        item._scene_attached = True

    # ---------------------------------------------------------------- Wire value display
    def set_show_wire_values(self, show: bool):
        """Toggle live value display on all wires."""
        if not self.is_alive():
            return
        self._show_wire_values = show
        for wire_item in self._wire_items.values():
            wire_item.set_show_value(show)

    def update_wire_values(self):
        """Update live values on all wires (call when strategy is online)."""
        if not self._show_wire_values or not self.is_alive():
            return
        for wire_item in self._wire_items.values():
            wire_item.update_live_value()

    # ---------------------------------------------------------------- Block search
    def find_blocks_by_name(self, query: str) -> list[BlockItem]:
        """Find block items matching query against name, type, or tag (case-insensitive)."""
        if not query or not self.is_alive():
            return []
        q = query.lower()
        results = []
        for block_item in self._block_items.values():
            block = block_item.block
            # Match instance name
            if q in block.instance_name.lower():
                results.append(block_item)
                continue
            # Match block type
            if q in block.block_type.lower():
                results.append(block_item)
                continue
            # Match configured tag (IO blocks store tag in config)
            tag = block.config.params.get("tag", "")
            if tag and q in str(tag).lower():
                results.append(block_item)
                continue
        return sorted(results, key=lambda bi: bi.block.instance_name.lower())

    _HIGHLIGHT_RGB = (255, 215, 0)   # BlockItem's gold search glow

    def apply_search_highlights(self, block_items):
        """Highlight exactly ``block_items`` and clear any previous highlight.

        Preferred over calling ``BlockItem.set_search_highlight(True)``
        directly, because the scene then knows which blocks are lit and the
        next clear costs O(hits) instead of O(blocks).
        """
        if not self.is_alive():
            return
        wanted = {bi.block.id: bi for bi in block_items}
        for bid in list(self._search_highlighted):
            if bid in wanted:
                continue
            item = self._block_items.get(bid)
            if item is not None:
                item.set_search_highlight(False)
            self._search_highlighted.discard(bid)
        for bid, item in wanted.items():
            if bid in self._search_highlighted:
                continue          # already lit — do not reallocate the effect
            item.set_search_highlight(True)
            self._search_highlighted.add(bid)

    def _looks_highlighted(self, item: BlockItem) -> bool:
        """Cheap read-only test for the gold search glow.

        ``set_search_highlight()`` allocates a new QGraphicsDropShadowEffect on
        every call, including when switching it *off*, so on a 300-block module
        the per-keystroke clear used to build 300 effect objects.  Inspecting
        the current effect costs nothing and lets the sweep skip blocks that
        were never lit.
        """
        effect = item.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            return False
        c = effect.color()
        return (c.red(), c.green(), c.blue()) == self._HIGHLIGHT_RGB

    def clear_search_highlights(self):
        """Remove search highlights from all block items."""
        if not self.is_alive():
            return
        for block_id, block_item in self._block_items.items():
            if (block_id in self._search_highlighted
                    or self._looks_highlighted(block_item)):
                block_item.set_search_highlight(False)
        self._search_highlighted.clear()

    # ---------------------------------------------------------------- Selection
    def _on_block_selected(self, block_id: str):
        self.selectionUpdated.emit(block_id)

    def _on_block_double_clicked(self, block_id: str):
        """Default: open properties for the block. Composite blocks fire
        a separate signal so the designer can open the interior in a tab."""
        blk = self._graph.blocks.get(block_id)
        if blk is not None and blk.block_type == "COMPOSITE":
            self.compositeDrillDownRequested.emit(block_id)
            return
        self.selectionUpdated.emit(block_id)

    def get_selected_block(self) -> FunctionBlock | None:
        for item in self.selectedItems():
            if isinstance(item, BlockItem):
                return item.block
        return None

    def selected_composite_candidates(self) -> list[BlockItem]:
        """Return selected blocks in graph order when they may be grouped.

        ``selectedItems()`` follows graphics stacking order, which can change
        after a repaint. Graph order is stable, so repeated grouping derives
        the same boundary-port order and suffixes. Parameter Special Items are
        deliberately all-or-nothing: silently leaving one selected item
        outside the composite would make the marquee selection lie.
        """
        selected = {
            item.block.id: item
            for item in self.selectedItems()
            if isinstance(item, BlockItem)
        }
        if any(item._is_special_palette_item for item in selected.values()):
            return []
        return [selected[block_id] for block_id in self._graph.blocks
                if block_id in selected]

    def _next_composite_name(self) -> str:
        existing = {
            block.instance_name.casefold()
            for block in self._graph.blocks.values()
        }
        number = 1
        while f"composite_{number}" in existing:
            number += 1
        return f"Composite_{number}"

    def create_composite_from_selection(
        self,
        composite_name: str | None = None,
    ) -> str | None:
        """Prompt if needed, then replace the marquee selection with a composite."""
        if self._refuse_structural_edit("create a composite from the selection"):
            return None

        candidates = self.selected_composite_candidates()
        if len(candidates) < 2:
            self._notice(
                "Create Composite: select at least two ordinary blocks first")
            return None

        if composite_name is None:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if is_headless():
                log.info("Composite naming prompt skipped headlessly")
                return None
            from PySide6.QtWidgets import QInputDialog
            views = self.views()
            parent = views[0] if views else None
            composite_name, accepted = QInputDialog.getText(
                parent,
                "Create Composite from Selection",
                f"Composite name ({len(candidates)} selected blocks):",
                text=self._next_composite_name(),
            )
            if not accepted:
                return None

        composite_name = str(composite_name).strip()
        if not composite_name:
            return None

        composite_id = self.group_into_composite(
            [item.block.id for item in candidates], composite_name)
        if composite_id is None:
            return None

        # The selected blocks no longer exist on the parent canvas. Selecting
        # their replacement makes the result and the next drill-down gesture
        # obvious, matching subsystem creation in diagramming tools.
        self.clearSelection()
        composite_item = self._block_items.get(composite_id)
        if composite_item is not None:
            composite_item.setSelected(True)
        return composite_id

    def _add_create_composite_action(self, menu):
        """Add the shared marquee-selection command when it is applicable."""
        candidates = self.selected_composite_candidates()
        if len(candidates) < 2:
            return None
        action = menu.addAction(
            f"Create Composite from Selection...  ({len(candidates)} blocks)")
        action.setEnabled(not self._structure_locked)
        action.triggered.connect(
            lambda _checked=False: self.create_composite_from_selection())
        return action

    # ---------------------------------------------------------------- Context Menu
    def contextMenuEvent(self, event):
        """Scene-level context menu (right-click on empty area)."""
        views = self.views()
        transform = views[0].transform() if views else QTransform()
        item = self.itemAt(event.scenePos(), transform)
        if item:
            super().contextMenuEvent(event)
            return

        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        menu = studio_menu("MODULE",
                           getattr(self.graph, "name", "") or "diagram")

        pos = event.scenePos()

        # Add block submenus by category
        from azeo_control_trainer.core.strategy.model.block_base import BlockCategory
        by_cat = registry.by_category()
        cat_order = [BlockCategory.IO, BlockCategory.CONTROL, BlockCategory.MATH,
                     BlockCategory.SIGNAL, BlockCategory.LOGIC, BlockCategory.SAFETY,
                     BlockCategory.COMPOSITE]

        for cat in cat_order:
            blocks = by_cat.get(cat, [])
            if not blocks:
                continue
            cat_menu = menu.addMenu(f"Add {cat.value}")
            for block_cls in sorted(blocks, key=lambda c: c.display_name):
                act = cat_menu.addAction(block_cls.display_name)
                act.triggered.connect(
                    lambda checked, bt=block_cls.block_type, p=pos:
                        self.add_block_by_type(bt, p)
                )

        menu.addSeparator()

        # Add comment
        act_comment = menu.addAction("Add Comment")
        act_comment.triggered.connect(
            lambda checked, p=pos: self.add_comment("Comment", p))

        # Selection commands remain available when the user right-clicks the
        # empty space around a rubber-band selection, not only a block face.
        selected_blocks = [
            item for item in self.selectedItems()
            if isinstance(item, BlockItem)
        ]
        if selected_blocks:
            menu.addSeparator()
            self._add_create_composite_action(menu)
            act_template = menu.addAction(
                f"Save Selection as Template ({len(selected_blocks)} blocks)...")
            act_template.triggered.connect(self.saveTemplateRequested.emit)

        menu.addSeparator()
        act_compile = menu.addAction("Compile Strategy")
        act_compile.triggered.connect(self.compileRequested.emit)

        menu.exec_transient(event.screenPos())

    # ---------------------------------------------------------------- Copy / Paste
    def copy_selected(self) -> dict | None:
        """Serialize every selected engineering object.

        Blocks, annotations and explicitly selected connections all belong to
        the drawing.  The former block-only clipboard made Copy/Cut silently
        do nothing for a note or a standalone wire even though Select All and
        Delete included them.  Relative positions keep mixed selections
        together when they are pasted at a new anchor.
        """
        selected = list(self.selectedItems())
        selected_blocks = [item for item in selected
                           if isinstance(item, BlockItem)]
        selected_comments = [item for item in selected
                             if isinstance(item, CommentItem)]
        selected_wire_items = [item for item in selected
                               if isinstance(item, WireItem)]
        if not (selected_blocks or selected_comments or selected_wire_items):
            return None

        selected_ids = {item.block.id for item in selected_blocks}

        anchors = [(item.block.x, item.block.y) for item in selected_blocks]
        anchors.extend((item.pos().x(), item.pos().y())
                       for item in selected_comments)
        if not anchors:
            anchors = [
                (item.sceneBoundingRect().center().x(),
                 item.sceneBoundingRect().center().y())
                for item in selected_wire_items
            ]
        cx = sum(x for x, _y in anchors) / len(anchors)
        cy = sum(y for _x, y in anchors) / len(anchors)

        # Serialize blocks with positions relative to centroid
        block_datas = []
        for item in selected_blocks:
            bd = item.block.to_dict()
            bd["_rel_x"] = item.block.x - cx
            bd["_rel_y"] = item.block.y - cy
            block_datas.append(bd)

        comment_datas = []
        for item in selected_comments:
            data = item.serialize()
            data["_rel_x"] = data["x"] - cx
            data["_rel_y"] = data["y"] - cy
            comment_datas.append(data)

        # Connections between copied blocks travel automatically.  An
        # explicitly selected standalone wire also travels, retaining its
        # original endpoint identities so Cut -> Paste can reconnect it.
        explicit_wire_ids = {item.wire.id for item in selected_wire_items}
        wire_datas = []
        for wire in self._graph.wires.values():
            endpoints_selected = (
                wire.src_block_id in selected_ids
                and wire.dst_block_id in selected_ids
            )
            if endpoints_selected or wire.id in explicit_wire_ids:
                data = wire.to_dict()
                data["_explicit"] = wire.id in explicit_wire_ids
                wire_datas.append(data)

        log.info("Copied %d blocks, %d wires, %d comments",
                 len(block_datas), len(wire_datas), len(comment_datas))
        return {"blocks": block_datas, "wires": wire_datas,
                "comments": comment_datas,
                "centroid_x": cx, "centroid_y": cy}

    def paste_clipboard(self, clipboard_data: dict,
                        target_pos: QPointF | None = None,
                        offset: QPointF | None = None) -> list[str]:
        """Deserialize clipboard data, creating new blocks and wires.

        Args:
            clipboard_data: Dict from copy_selected().
            target_pos: If given, paste centered at this scene position.
            offset: If given (and target_pos is None), offset from original
                    centroid.  Defaults to (40, 40).

        Returns:
            List of new block IDs that were created.
        """
        if not clipboard_data or not any(
            clipboard_data.get(kind) for kind in ("blocks", "wires", "comments")
        ):
            return []
        # Blocks and wires are structural; comments deliberately remain legal
        # online. A mixed clipboard stays atomic rather than quietly pasting
        # only its harmless-looking subset.
        has_structure = bool(
            clipboard_data.get("blocks") or clipboard_data.get("wires")
        )
        if has_structure and self._refuse_structural_edit("paste selection"):
            return []

        if offset is None:
            offset = QPointF(40, 40)

        old_cx = clipboard_data.get("centroid_x", 0)
        old_cy = clipboard_data.get("centroid_y", 0)

        if target_pos is not None:
            anchor_x = target_pos.x()
            anchor_y = target_pos.y()
        else:
            anchor_x = old_cx + offset.x()
            anchor_y = old_cy + offset.y()

        # Build old_id -> new_id mapping and collect existing instance names
        id_map: dict[str, str] = {}
        existing_names = {b.instance_name for b in self._graph.blocks.values()}

        # Pre-generate new IDs
        for bd in clipboard_data.get("blocks", []):
            old_id = bd["id"]
            id_map[old_id] = uuid.uuid4().hex[:12]

        # Group all additions into a single undo macro
        self._undo_stack.beginMacro("Paste Selection")

        new_block_ids = []
        new_wire_items = []
        new_comment_items = []
        skipped: list[str] = []
        for bd in clipboard_data.get("blocks", []):
            old_id = bd["id"]
            new_id = id_map[old_id]

            # Look up the block class from the registry
            block_cls = registry.get(bd["block_type"])
            if block_cls is None:
                skipped.append(str(bd.get("block_type")))
                continue

            # Generate a unique instance name
            base_name = bd.get("instance_name", bd["block_type"])
            new_name = self._unique_name(base_name, existing_names)
            existing_names.add(new_name)

            # Create the block via from_dict, but override id and name
            block_data = dict(bd)
            block_data["id"] = new_id
            block_data["instance_name"] = new_name

            # Calculate new position from relative offset
            rel_x = bd.get("_rel_x", 0)
            rel_y = bd.get("_rel_y", 0)
            block_data["x"] = anchor_x + rel_x
            block_data["y"] = anchor_y + rel_y

            block = block_cls.from_dict(block_data)
            pos = QPointF(block.x, block.y)
            self._add_block_internal(block, pos)
            self._undo_stack.push(AddBlockCommand(self, block, pos))
            new_block_ids.append(new_id)

        # Re-create wires with mapped block IDs
        dropped_wires = 0
        for wd in clipboard_data.get("wires", []):
            old_src = wd["src_block_id"]
            old_dst = wd["dst_block_id"]
            src_was_copied = old_src in id_map
            dst_was_copied = old_dst in id_map
            new_src = id_map.get(old_src)
            new_dst = id_map.get(old_dst)
            # Only an explicitly selected connection may reuse an endpoint
            # that was not itself copied. This makes wire-only Cut/Paste
            # useful without making an implicit group connection escape.
            if wd.get("_explicit"):
                if new_src is None and old_src in self._graph.blocks:
                    new_src = old_src
                if new_dst is None and old_dst in self._graph.blocks:
                    new_dst = old_dst
            if not new_src or not new_dst:
                dropped_wires += 1
                continue
            # Check both blocks were actually created
            if new_src not in self._graph.blocks or new_dst not in self._graph.blocks:
                dropped_wires += 1
                continue
            route_points = wd.get("route_points")
            if route_points and src_was_copied and dst_was_copied:
                dx = anchor_x - old_cx
                dy = anchor_y - old_cy
                route_points = [
                    [float(point[0]) + dx, float(point[1]) + dy]
                    for point in route_points
                ]
            elif src_was_copied != dst_was_copied:
                # One endpoint moved and one stayed; recompute bends rather
                # than retaining coordinates tied to the old geometry.
                route_points = None
            wire_item = self._add_wire_internal(
                new_src, wd["src_terminal"],
                new_dst, wd["dst_terminal"],
                wd.get("is_bkcal", False),
                allow_type_mismatch=True,
                route_points=route_points)
            if wire_item:
                self._undo_stack.push(AddWireCommand(
                    self, wire_item.wire.id,
                    new_src, wd["src_terminal"],
                    new_dst, wd["dst_terminal"],
                    wd.get("is_bkcal", False),
                    route_points=route_points))
                new_wire_items.append(wire_item)
            else:
                dropped_wires += 1

        for cd in clipboard_data.get("comments", []):
            data = dict(cd)
            data["x"] = anchor_x + float(cd.get("_rel_x", 0.0))
            data["y"] = anchor_y + float(cd.get("_rel_y", 0.0))
            item = CommentItem.deserialize(data)
            self._add_comment_internal(item)
            self._undo_stack.push(AddCommentCommand(
                self, item, "Paste Comment"))
            new_comment_items.append(item)

        self._undo_stack.endMacro()

        # A paste that silently produces fewer blocks or wires than were
        # copied is exactly the kind of quiet loss the operator must be told
        # about — the strategy looks pasted but is not the one they copied.
        if skipped:
            self._notice(
                f"Paste: {len(skipped)} block(s) were skipped — unknown block "
                f"type(s) {', '.join(sorted(set(skipped)))}")
        if dropped_wires:
            self._notice(
                f"Paste: {dropped_wires} connection(s) could not be recreated")

        # Select the complete pasted object set, matching mixed-object Copy.
        self.clearSelection()
        for bid in new_block_ids:
            item = self._block_items.get(bid)
            if item:
                item.setSelected(True)
        for item in new_wire_items:
            item.setSelected(True)
        for item in new_comment_items:
            item.setSelected(True)

        log.info("Pasted %d blocks, %d wires, %d comments",
                 len(new_block_ids), len(new_wire_items),
                 len(new_comment_items))
        return new_block_ids

    @staticmethod
    def _unique_name(base_name: str, existing: set[str]) -> str:
        """Generate a unique instance name by appending _copy or _N suffix."""
        # Strip existing _copy / _copy_N suffixes to get the root
        root = base_name
        m = re.match(r'^(.+?)(_copy(?:_\d+)?)$', root)
        if m:
            root = m.group(1)

        candidate = f"{root}_copy"
        if candidate not in existing:
            return candidate

        n = 2
        while True:
            candidate = f"{root}_copy_{n}"
            if candidate not in existing:
                return candidate
            n += 1

    # ---------------------------------------------------------------- Load/Clear
    def load_graph(self, graph: StrategyGraph, comments: list[dict] | None = None,
                   *, defer_items: bool = False):
        """Load a complete strategy graph into the scene.

        Args:
            graph: The strategy graph with blocks and wires.
            comments: Optional list of comment dicts from saved JSON.

        Refused while the module is on scan: the compiler and the runtime hold
        a reference to the *current* graph, so rebinding it here would leave a
        ghost strategy executing and writing MV tags with nothing on screen to
        show for it.
        """
        if self._refuse_structural_edit("load a different strategy"):
            return
        self._clear_all_internal()
        self._graph = graph
        # Unopened module tabs need their executable model and lossless comments,
        # but building all of their Qt items delays every application's startup.
        from copy import deepcopy
        self._deferred_comments = deepcopy(comments or [])
        if not defer_items:
            self.materialize_items()

    def materialize_items(self):
        """Build the diagram once, retaining the graph already used by its runtime.

        This is presentation work, so it is allowed for an online module. Never
        call load_graph here: replacing the graph would detach a running module.
        """
        if self._deferred_comments is None:
            return
        comments, self._deferred_comments = self._deferred_comments, None
        graph = self._graph

        # Add all blocks
        for block in graph.blocks.values():
            item = BlockItem(block)
            self.addItem(item)
            # Loading used to bypass _add_block_internal(), so an old or
            # externally generated coordinate could strand logic beyond the
            # page even though newly placed blocks were bounded.
            item.setPos(self.constrain_item_position(item, item.pos()))
            self._block_items[block.id] = item
            self._attach_block_item(item)

        # Add all wires
        hidden_wires = 0
        for wire in graph.wires.values():
            src_item = self._block_items.get(wire.src_block_id)
            dst_item = self._block_items.get(wire.dst_block_id)
            if not src_item or not dst_item:
                hidden_wires += 1
                continue
            src_ti = src_item.get_terminal_item("out", wire.src_terminal)
            dst_ti = dst_item.get_terminal_item("in", wire.dst_terminal)
            if not src_ti or not dst_ti:
                hidden_wires += 1
                continue
            wire_item = WireItem(wire, src_ti, dst_ti)
            self.addItem(wire_item)
            wire_item.update_position()
            self._wire_items[wire.id] = wire_item

        # Add comments
        if comments:
            for cdata in comments:
                item = CommentItem.deserialize(cdata)
                self.addItem(item)
                item.setPos(self.constrain_item_position(item, item.pos()))
                self._comment_items.append(item)
                self._attach_comment_item(item)

        # New items must inherit the canvas's display modes (live values on
        # the blocks are applied by _attach_block_item).
        if self._show_wire_values:
            self.set_show_wire_values(True)

        log.info("Loaded graph '%s': %d blocks, %d wires, %d comments",
                 graph.name, len(graph.blocks), len(graph.wires),
                 len(self._comment_items))
        if hidden_wires:
            # These wires still execute — they are in the graph — but nothing
            # on the canvas shows them, so say so instead of quietly drawing
            # an incomplete diagram.
            self._notice(
                f"{hidden_wires} connection(s) in '{graph.name}' are not drawn "
                f"— their block or terminal is missing from the canvas")

    def clear_all(self):
        """Remove all items from the scene and start an empty strategy."""
        if self._refuse_structural_edit("clear the canvas"):
            return
        self._clear_all_internal()
        self._graph = StrategyGraph("Untitled Strategy")

    def _clear_all_internal(self):
        """Tear the canvas down without the on-scan check.

        Order matters: the undo stack is cleared *first* so its commands drop
        their references to the items before ``QGraphicsScene.clear()``
        destroys the C++ objects underneath them — otherwise a trimmed command
        is left holding a wrapper whose C++ half is gone.
        """
        self._undo_stack.clear()
        for item in self._block_items.values():
            self._detach_block_item(item)
        for comment in self._comment_items:
            if getattr(comment, "_scene_attached", False):
                try:
                    comment.positionChanged.disconnect(self.notify_modified)
                except (RuntimeError, TypeError):
                    pass
                comment._scene_attached = False
        self._block_items.clear()
        self._wire_items.clear()
        self._comment_items.clear()
        self._deferred_comments = None
        self._search_highlighted.clear()
        self.clear()

    def get_comments_data(self) -> list[dict]:
        """Return serializable list of comment dicts."""
        if self._deferred_comments is not None:
            from copy import deepcopy
            return deepcopy(self._deferred_comments)
        return [c.serialize() for c in self._comment_items]

    def get_block_item(self, block_id: str) -> BlockItem | None:
        return self._block_items.get(block_id)

    # ---------------------------------------------------------------- Composites
    def _snapshot_slice(self, block_ids: list[str]):
        """Snapshot the given blocks plus every wire touching them.

        See :class:`CompositeStructureCommand` for the format.
        """
        ids = set(block_ids)
        blocks = []
        for bid in block_ids:
            blk = self._graph.blocks.get(bid)
            if blk is not None:
                blocks.append((blk, blk.x, blk.y))
        wires = [
            (w.id, w.src_block_id, w.src_terminal,
             w.dst_block_id, w.dst_terminal, w.is_bkcal)
            for w in self._graph.wires.values()
            if w.src_block_id in ids or w.dst_block_id in ids
        ]
        return blocks, wires

    def group_into_composite(self, block_ids: list[str],
                              composite_name: str = "Composite") -> str | None:
        """Collapse the given blocks into a new CompositeBlock and resync
        the scene. Returns the new composite's id (or None on failure).

        The whole operation is pushed onto the undo stack as a single
        command, so Ctrl+Z puts the original blocks and wires back instead
        of undoing whatever command happened to precede the grouping.
        """
        if self._refuse_structural_edit("group blocks into a composite"):
            return None
        if not block_ids:
            return None

        before = self._snapshot_slice(block_ids)
        composite_id = self._group_internal(block_ids, composite_name)
        if composite_id is None:
            self._notice("The selected blocks could not be grouped into a "
                         "composite — nothing was changed")
            return None
        after = self._snapshot_slice([composite_id])
        self._undo_stack.push(CompositeStructureCommand(
            self, before, after,
            f"Group {len(block_ids)} Blocks into Composite"))
        return composite_id

    def _group_internal(self, block_ids: list[str],
                        composite_name: str = "Composite") -> str | None:
        """Group without pushing to the undo stack.

        Implementation: delegate the graph surgery to
        :func:`collapse_to_composite`, then surgically update the scene
        — remove BlockItems and WireItems that were moved into the
        composite, add a single new BlockItem for the composite, and
        spawn fresh WireItems for the external wires that now connect
        to the composite's auto-derived pins.
        """
        from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
            collapse_to_composite,
        )
        if not block_ids:
            return None

        # Snapshot wires touching the selection BEFORE surgery — we'll
        # remove their WireItems regardless of whether they end up
        # internal or re-routed externally.
        selected = set(block_ids)
        wires_to_remove_items: set[str] = set()
        for w in self._graph.wires.values():
            if w.src_block_id in selected or w.dst_block_id in selected:
                wires_to_remove_items.add(w.id)

        composite = collapse_to_composite(self._graph, block_ids, composite_name)
        if composite is None:
            return None

        # Remove WireItems for any wire that's no longer in the parent graph
        for wid in list(wires_to_remove_items):
            item = self._wire_items.pop(wid, None)
            if item is not None:
                self.removeItem(item)

        # Remove BlockItems for the moved blocks
        for bid in block_ids:
            item = self._block_items.pop(bid, None)
            if item is not None:
                self._detach_block_item(item)
                self.removeItem(item)

        # Spawn the BlockItem for the new composite
        comp_item = BlockItem(composite)
        self.addItem(comp_item)
        self._block_items[composite.id] = comp_item
        self._attach_block_item(comp_item)

        # Spawn WireItems for any new external wires the model added.
        # (The model's collapse_to_composite() created brand-new Wire
        # objects with fresh IDs for the external segments, so anything
        # in self._graph.wires that isn't already in _wire_items is new.)
        for wire in list(self._graph.wires.values()):
            if wire.id in self._wire_items:
                continue
            src_item = self._block_items.get(wire.src_block_id)
            dst_item = self._block_items.get(wire.dst_block_id)
            if not src_item or not dst_item:
                continue
            src_ti = src_item.get_terminal_item("out", wire.src_terminal)
            dst_ti = dst_item.get_terminal_item("in", wire.dst_terminal)
            if not src_ti or not dst_ti:
                continue
            witem = WireItem(wire, src_ti, dst_ti)
            self.addItem(witem)
            self._wire_items[wire.id] = witem

        self.blockAdded.emit(composite.id)
        self.strategyModified.emit()
        log.info("Scene grouped %d blocks into composite '%s' (%s)",
                 len(block_ids), composite_name, composite.id)
        return composite.id

    def ungroup_composite(self, composite_id: str) -> list[str] | None:
        """Inverse of :meth:`group_into_composite` — replace a composite
        with its interior. Returns the list of promoted block ids.

        Undoable: the composite and its external wires come back on Ctrl+Z.
        """
        if self._refuse_structural_edit("ungroup a composite"):
            return None
        if composite_id not in self._graph.blocks:
            return None

        before = self._snapshot_slice([composite_id])
        promoted = self._ungroup_internal(composite_id)
        if promoted is None:
            self._notice("That block could not be ungrouped — it is not a "
                         "composite, or its interior is empty")
            return None
        after = self._snapshot_slice(promoted)
        self._undo_stack.push(CompositeStructureCommand(
            self, before, after, "Ungroup Composite"))
        return promoted

    def _ungroup_internal(self, composite_id: str) -> list[str] | None:
        """Ungroup without pushing to the undo stack."""
        from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
            CompositeBlock, explode_composite,
        )
        comp = self._graph.blocks.get(composite_id)
        if not isinstance(comp, CompositeBlock):
            return None

        # Capture wire ids touching the composite + composite item ref
        wires_to_remove: set[str] = set()
        for w in self._graph.wires.values():
            if w.src_block_id == composite_id or w.dst_block_id == composite_id:
                wires_to_remove.add(w.id)
        comp_item = self._block_items.pop(composite_id, None)

        promoted = explode_composite(self._graph, composite_id)
        if promoted is None:
            # restore item reference if explode failed
            if comp_item is not None:
                self._block_items[composite_id] = comp_item
            return None

        # Remove the composite's WireItems + BlockItem
        for wid in wires_to_remove:
            item = self._wire_items.pop(wid, None)
            if item is not None:
                self.removeItem(item)
        if comp_item is not None:
            self._detach_block_item(comp_item)
            self.removeItem(comp_item)

        # Spawn BlockItems for each promoted block
        for bid in promoted:
            blk = self._graph.blocks.get(bid)
            if blk is None:
                continue
            item = BlockItem(blk)
            self.addItem(item)
            self._block_items[bid] = item
            self._attach_block_item(item)

        # Spawn WireItems for every wire now in the graph that doesn't
        # already have a graphics item (this catches re-routed externals
        # and any internal wires that moved out of the composite).
        for wire in list(self._graph.wires.values()):
            if wire.id in self._wire_items:
                continue
            src_item = self._block_items.get(wire.src_block_id)
            dst_item = self._block_items.get(wire.dst_block_id)
            if not src_item or not dst_item:
                continue
            src_ti = src_item.get_terminal_item("out", wire.src_terminal)
            dst_ti = dst_item.get_terminal_item("in", wire.dst_terminal)
            if not src_ti or not dst_ti:
                continue
            witem = WireItem(wire, src_ti, dst_ti)
            self.addItem(witem)
            self._wire_items[wire.id] = witem

        self.blockRemoved.emit(composite_id)
        self.strategyModified.emit()
        log.info("Scene ungrouped composite '%s' -> %d blocks promoted",
                 composite_id, len(promoted))
        return promoted

    # ---------------------------------------------------------------- Live mode (Azeo-style)
    def set_live_mode(self, enabled: bool):
        """Toggle Azeo-style live monitoring on all blocks."""
        self._live_mode = bool(enabled)
        if not self.is_alive():
            return
        for item in self._block_items.values():
            item.set_live_mode(enabled)

    def is_live_mode(self) -> bool:
        """True if blocks are in live-monitoring mode."""
        return self._live_mode

    def set_show_exec_order(self, show: bool):
        """Toggle execution order badge on all blocks."""
        if not self.is_alive():
            return
        for item in self._block_items.values():
            item.set_show_exec_order(show)

    def apply_exec_order(self, exec_order: list[str]):
        """Set _exec_order on blocks from compiled execution order."""
        if not self.is_alive():
            return
        for i, block_id in enumerate(exec_order):
            block = self._graph.blocks.get(block_id)
            if block:
                block._exec_order = i

    # ---------------------------------------------------------------- Alignment tools
    def align_selected(self, direction: str):
        """Align selected blocks: 'left', 'right', 'top', 'bottom', 'center_h', 'center_v'."""
        selected = [item for item in self.selectedItems()
                    if isinstance(item, BlockItem)]
        if len(selected) < 2:
            self._notice("Align: select at least two blocks first")
            return

        self._undo_stack.beginMacro(f"Align {direction}")

        if direction == "left":
            target = min(item.pos().x() for item in selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(target, item.pos().y())
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        elif direction == "right":
            target = max(item.pos().x() + item._width for item in selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(target - item._width, item.pos().y())
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        elif direction == "top":
            target = min(item.pos().y() for item in selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(item.pos().x(), target)
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        elif direction == "bottom":
            target = max(item.pos().y() + item._height for item in selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(item.pos().x(), target - item._height)
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        elif direction == "center_h":
            avg_cx = sum(item.pos().x() + item._width / 2
                         for item in selected) / len(selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(avg_cx - item._width / 2, item.pos().y())
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        elif direction == "center_v":
            avg_cy = sum(item.pos().y() + item._height / 2
                         for item in selected) / len(selected)
            for item in selected:
                old = QPointF(item.pos())
                item.setPos(item.pos().x(), avg_cy - item._height / 2)
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))

        self._undo_stack.endMacro()
        self.update_all_wires()

    def distribute_selected(self, axis: str):
        """Distribute selected blocks evenly: 'horizontal' or 'vertical'."""
        selected = [item for item in self.selectedItems()
                    if isinstance(item, BlockItem)]
        if len(selected) < 3:
            self._notice("Distribute: select at least three blocks first")
            return

        self._undo_stack.beginMacro(f"Distribute {axis}")

        if axis == "horizontal":
            selected.sort(key=lambda item: item.pos().x())
            left = selected[0].pos().x()
            right = selected[-1].pos().x()
            spacing = (right - left) / (len(selected) - 1)
            for i, item in enumerate(selected):
                old = QPointF(item.pos())
                item.setPos(left + i * spacing, item.pos().y())
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))
        else:
            selected.sort(key=lambda item: item.pos().y())
            top = selected[0].pos().y()
            bottom = selected[-1].pos().y()
            spacing = (bottom - top) / (len(selected) - 1)
            for i, item in enumerate(selected):
                old = QPointF(item.pos())
                item.setPos(item.pos().x(), top + i * spacing)
                self._undo_stack.push(MoveBlockCommand(
                    self, item.block.id, old, QPointF(item.pos())))

        self._undo_stack.endMacro()
        self.update_all_wires()

    def auto_arrange(self):
        """Auto-arrange blocks left-to-right using topological order.

        A half-built strategy is exactly what you reach for Auto Arrange on —
        and exactly what fails to compile.  When the compile fails we lay the
        blocks out from the raw wire graph instead (ignoring cycles) and say
        so, rather than leaving a button that appears broken.
        """
        from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy, CompileError
        try:
            compiled = compile_strategy(self._graph)
            exec_order = list(compiled.exec_order)
            forward_wires = list(compiled.forward_wires)
        except CompileError as e:
            exec_order = list(self._graph.blocks.keys())
            forward_wires = [w for w in self._graph.wires.values()
                             if not w.is_bkcal]
            self._notice(f"Auto Arrange: the strategy does not compile ({e}) "
                         f"— arranged from the drawn connections instead")

        if not exec_order:
            self._notice("Auto Arrange: there is nothing to arrange")
            return

        # Predecessor index, built once: the depth pass used to rescan every
        # forward wire for every block (O(blocks x wires) — 57 ms at 60
        # blocks, seconds on a real module).
        preds: dict[str, list[str]] = {}
        for wire in forward_wires:
            preds.setdefault(wire.dst_block_id, []).append(wire.src_block_id)

        # Compute depth (layer) for each block
        depths: dict[str, int] = {}
        for block_id in exec_order:
            max_pred_depth = -1
            for src_id in preds.get(block_id, ()):
                max_pred_depth = max(max_pred_depth, depths.get(src_id, 0))
            depths[block_id] = max_pred_depth + 1

        # Group blocks by depth layer
        layers: dict[int, list[str]] = {}
        for block_id, depth in depths.items():
            layers.setdefault(depth, []).append(block_id)

        # Sort within each layer by original y position (preserve vertical order)
        for layer in layers.values():
            layer.sort(key=lambda bid: self._graph.blocks[bid].y
                       if bid in self._graph.blocks else 0)

        # Position blocks
        self._undo_stack.beginMacro("Auto Arrange")
        x_spacing = 220
        y_spacing = 100
        base_x = -len(layers) * x_spacing / 2
        base_y = 0

        for depth in sorted(layers.keys()):
            block_ids = layers[depth]
            x = base_x + depth * x_spacing
            total_h = len(block_ids) * y_spacing
            start_y = base_y - total_h / 2

            for i, block_id in enumerate(block_ids):
                item = self._block_items.get(block_id)
                if not item:
                    continue
                old = QPointF(item.pos())
                new_pos = self.snap_to_grid(QPointF(x, start_y + i * y_spacing))
                item.setPos(new_pos)
                self._undo_stack.push(MoveBlockCommand(
                    self, block_id, old, new_pos))

        self._undo_stack.endMacro()
        self.update_all_wires()
