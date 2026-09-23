"""Undo/Redo command classes for the strategy designer.

Uses Qt's QUndoCommand / QUndoStack to provide Ctrl+Z / Ctrl+Shift+Z
support for strategy operations: add/delete/move blocks, add/delete wires,
config changes, block rename and comment annotations.

Two invariants every command here must hold:

1. **Scene and model stay in step.**  Commands never touch ``StrategyGraph``
   directly; they call the scene's ``_*_internal`` helpers, which mutate the
   graph *and* the graphics items together.  A command that cannot find its
   target logs a warning instead of returning silently — a silent no-op means
   the undo stack and the graph have already diverged and the operator would
   never know.

2. **Every command marks the module modified.**  ``strategyModified`` is what
   drives the tab's dirty marker and the "the drawing no longer matches the
   downloaded module" state, so undo/redo must flip it exactly like the
   original edit did.  Commands whose work goes through a scene ``_internal``
   helper get that for free (those helpers emit); the ones that mutate a block
   in place (config, rename) call :func:`_notify` themselves.
"""
from __future__ import annotations

from copy import deepcopy
import logging
import time

import shiboken6
from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand

log = logging.getLogger("strategy.undo")


def _live_item(scene, block_id: str):
    """The block's graphics item, or None if it is gone.

    A command can outlive the canvas it was recorded on (the stack is trimmed
    at teardown, or the tab closed while an undo is in flight).  The Python
    wrapper survives the C++ item, so every item access has to be checked or
    it raises ``RuntimeError: Internal C++ object (BlockItem) already
    deleted``.
    """
    item = scene._block_items.get(block_id)
    if item is None or not shiboken6.isValid(item):
        return None
    return item


def _notify(scene) -> None:
    """Tell the scene the strategy changed, tolerating a torn-down scene.

    Commands can outlive the C++ scene (a stack trimmed at teardown), and test
    doubles do not implement the whole scene API — neither case should raise.
    """
    notify = getattr(scene, "notify_modified", None)
    if callable(notify):
        notify()


# ── Move Block Command ──────────────────────────────────────────────────

class MoveBlockCommand(QUndoCommand):
    """Undo/redo position change for a dragged block.

    Merging is **opt-in**.  One command is pushed per completed drag gesture
    (``BlockItem.mouseReleaseEvent``), so merging by block id alone collapsed
    two deliberate, unrelated repositionings — separated by any amount of other
    work — into a single undo step.  Callers that genuinely emit a stream of
    intermediate positions within one gesture pass a shared ``merge_id``.
    """

    _NO_MERGE = -1

    def __init__(self, scene, block_id: str,
                 old_pos: QPointF, new_pos: QPointF,
                 description: str = "Move Block",
                 merge_id: int | None = None):
        super().__init__(description)
        self._scene = scene
        self._block_id = block_id
        self._old_pos = QPointF(old_pos)
        self._new_pos = QPointF(new_pos)
        self._merge_id = self._NO_MERGE if merge_id is None else int(merge_id)

    def _move(self, pos: QPointF):
        item = _live_item(self._scene, self._block_id)
        if item is None:
            log.warning("MoveBlockCommand: block %s is no longer on the "
                        "canvas — undo history and graph have diverged",
                        self._block_id)
            return
        item.setPos(pos)
        item.block.x = pos.x()
        item.block.y = pos.y()
        _notify(self._scene)

    def undo(self):
        self._move(self._old_pos)

    def redo(self):
        self._move(self._new_pos)

    def id(self) -> int:
        return self._merge_id

    def mergeWith(self, other: QUndoCommand) -> bool:
        if self._merge_id == self._NO_MERGE:
            return False
        if not isinstance(other, MoveBlockCommand):
            return False
        if other._block_id != self._block_id:
            return False
        # Keep our old_pos, take their new_pos
        self._new_pos = QPointF(other._new_pos)
        return True


# ── Add Block Command ───────────────────────────────────────────────────

class AddBlockCommand(QUndoCommand):
    """Undo block addition by removing from scene; redo by re-adding.

    On undo, also removes any wires connected to the block.
    """

    def __init__(self, scene, block, pos=None,
                 description: str = "Add Block"):
        super().__init__(description)
        self._scene = scene
        self._block = block
        self._pos = pos
        self._first_redo = True  # Block already added on first push

    def undo(self):
        self._scene._delete_block_internal(self._block.id)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return  # Block was already added before pushing command
        self._scene._add_block_internal(self._block, self._pos)


# ── Delete Block Command ────────────────────────────────────────────────

class DeleteBlockCommand(QUndoCommand):
    """Undo block deletion by re-creating block and its connected wires.

    Unlike the add commands this has **no** ``_first_redo`` guard: the scene
    pushes it *before* deleting, so ``QUndoStack.push`` → ``redo()`` performs
    the one and only deletion.  (The scene used to delete a second time after
    the push, which fired ``blockRemoved`` twice for a single delete.)
    """

    def __init__(self, scene, block, connected_wires: list,
                 description: str = "Delete Block"):
        super().__init__(description)
        self._scene = scene
        self._block = block
        self._block_pos = QPointF(block.x, block.y)
        # Snapshot wire data for re-creation
        self._wire_snapshots = [
            (w.id, w.src_block_id, w.src_terminal,
             w.dst_block_id, w.dst_terminal, w.is_bkcal,
             deepcopy(w.route_points))
            for w in connected_wires
        ]

    def undo(self):
        # Re-add the block
        self._scene._add_block_internal(self._block, self._block_pos)
        # Re-add the wires
        for (wid, src_bid, src_t, dst_bid, dst_t, is_bkcal,
             route_points) in self._wire_snapshots:
            self._scene._add_wire_internal(
                src_bid, src_t, dst_bid, dst_t, is_bkcal, wire_id=wid,
                route_points=route_points)

    def redo(self):
        self._scene._delete_block_internal(self._block.id)


# ── Add Wire Command ────────────────────────────────────────────────────

class AddWireCommand(QUndoCommand):
    """Undo wire addition by removing it; redo by re-adding."""

    def __init__(self, scene, wire_id: str,
                 src_block_id: str, src_terminal: str,
                 dst_block_id: str, dst_terminal: str,
                 is_bkcal: bool = False,
                 description: str = "Add Wire",
                 route_points: list | None = None):
        super().__init__(description)
        self._scene = scene
        self._wire_id = wire_id
        self._src_block_id = src_block_id
        self._src_terminal = src_terminal
        self._dst_block_id = dst_block_id
        self._dst_terminal = dst_terminal
        self._is_bkcal = is_bkcal
        self._route_points = deepcopy(route_points)
        self._first_redo = True

    def undo(self):
        self._scene._delete_wire_internal(self._wire_id)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._scene._add_wire_internal(
            self._src_block_id, self._src_terminal,
            self._dst_block_id, self._dst_terminal,
            self._is_bkcal, wire_id=self._wire_id,
            route_points=self._route_points)


# ── Delete Wire Command ─────────────────────────────────────────────────

class DeleteWireCommand(QUndoCommand):
    """Undo wire deletion by re-creating it.

    Like :class:`DeleteBlockCommand`, the push itself performs the delete.
    """

    def __init__(self, scene, wire_id: str,
                 src_block_id: str, src_terminal: str,
                 dst_block_id: str, dst_terminal: str,
                 is_bkcal: bool = False,
                 description: str = "Delete Wire",
                 route_points: list | None = None):
        super().__init__(description)
        self._scene = scene
        self._wire_id = wire_id
        self._src_block_id = src_block_id
        self._src_terminal = src_terminal
        self._dst_block_id = dst_block_id
        self._dst_terminal = dst_terminal
        self._is_bkcal = is_bkcal
        self._route_points = deepcopy(route_points)

    def undo(self):
        self._scene._add_wire_internal(
            self._src_block_id, self._src_terminal,
            self._dst_block_id, self._dst_terminal,
            self._is_bkcal, wire_id=self._wire_id,
            route_points=self._route_points)

    def redo(self):
        self._scene._delete_wire_internal(self._wire_id)


# ── Change Config Command ───────────────────────────────────────────────

class ChangeConfigCommand(QUndoCommand):
    """Undo/redo config parameter changes via before/after snapshots.

    Supports merging so that a numeric field driven by ``valueChanged`` (one
    signal per keystroke: ``1`` → ``12`` → ``12.5``) collapses into a single
    undo entry instead of burying the previous real operation — and instead of
    consuming the stack's 200-entry limit with keystrokes.  Merging is keyed on
    the block **and the exact set of parameters that changed**, and is bounded
    in time so two deliberate edits of the same field stay separate.
    """

    _CMD_ID = 2002
    _MERGE_WINDOW_S = 1.0

    def __init__(self, scene, block_id: str,
                 old_config: dict, new_config: dict,
                 description: str = "Change Config"):
        super().__init__(description)
        self._scene = scene
        self._block_id = block_id
        self._old_config = dict(old_config)
        self._new_config = dict(new_config)
        self._changed_keys = frozenset(
            k for k in set(self._old_config) | set(self._new_config)
            if self._old_config.get(k) != self._new_config.get(k)
        )
        self._stamp = time.monotonic()

    def undo(self):
        self._apply(self._old_config)

    def redo(self):
        self._apply(self._new_config)

    def id(self) -> int:
        return self._CMD_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, ChangeConfigCommand):
            return False
        if other._block_id != self._block_id:
            return False
        if other._changed_keys != self._changed_keys:
            return False
        if other._stamp - self._stamp > self._MERGE_WINDOW_S:
            return False
        # Keep our "before", take their "after"
        self._new_config = dict(other._new_config)
        self._stamp = other._stamp
        return True

    def _apply(self, config: dict):
        block = self._scene._graph.blocks.get(self._block_id)
        if block is None:
            log.warning("ChangeConfigCommand: block %s is no longer in the "
                        "graph — the config change could not be applied",
                        self._block_id)
            return
        block.config.params = dict(config)
        block._apply_config()
        item = _live_item(self._scene, self._block_id)
        if item is not None:
            item.refresh()
        _notify(self._scene)


class BatchChangeConfigCommand(QUndoCommand):
    """Apply one parameter edit to several selected blocks as one undo step."""

    def __init__(self, scene, snapshots: list[tuple[str, dict, dict]],
                 description: str = "Change Multiple Block Parameters"):
        super().__init__(description)
        self._scene = scene
        self._snapshots = [
            (block_id, dict(before), dict(after))
            for block_id, before, after in snapshots
        ]

    def undo(self):
        self._apply(index=1)

    def redo(self):
        self._apply(index=2)

    def _apply(self, *, index: int):
        for snapshot in self._snapshots:
            block_id = snapshot[0]
            block = self._scene._graph.blocks.get(block_id)
            if block is None:
                log.warning("Batch config target %s no longer exists", block_id)
                continue
            block.config.params = dict(snapshot[index])
            block._apply_config()
            item = _live_item(self._scene, block_id)
            if item is not None:
                item.refresh()
        _notify(self._scene)


# ── Replace Block Command ────────────────────────────────────────────────

# Identity-preserving structural replacement command
class ReplaceBlockCommand(QUndoCommand):
    """Undo/redo an identity-preserving engineering block conversion.

    This is intentionally one command, not a delete plus an add: the latter
    exposes an intermediate graph with dangling wires and gives the user two
    undo steps for one assignment gesture.
    """

    def __init__(
        self,
        scene,
        previous,
        replacement,
        *,
        input_terminal_map: dict[str, str] | None = None,
        output_terminal_map: dict[str, str] | None = None,
        description: str = "Replace Block",
    ):
        super().__init__(description)
        self._scene = scene
        self._previous = previous
        self._replacement = replacement
        self._input_map = dict(input_terminal_map or {})
        self._output_map = dict(output_terminal_map or {})
        self._reverse_input_map = {
            target: source for source, target in self._input_map.items()
        }
        self._reverse_output_map = {
            target: source for source, target in self._output_map.items()
        }

    def undo(self):
        self._scene._replace_block_internal(
            self._previous,
            input_terminal_map=self._reverse_input_map,
            output_terminal_map=self._reverse_output_map,
        )

    def redo(self):
        self._scene._replace_block_internal(
            self._replacement,
            input_terminal_map=self._input_map,
            output_terminal_map=self._output_map,
        )


class ChangeTerminalVisibilityCommand(QUndoCommand):
    """Undo/redo the visible-pin set for one block.

    Pin visibility is persisted in each ``Terminal`` and changes both the
    engineering drawing and the block's calculated footprint. Applying it
    directly from the context menu bypassed the dirty marker and made the
    change impossible to undo. A complete before/after snapshot also makes
    bulk ``Show All`` and ``Hide Unused`` one coherent operation.
    """

    def __init__(
        self,
        scene,
        block_id: str,
        old_visibility: dict[tuple[str, str], bool],
        new_visibility: dict[tuple[str, str], bool],
        description: str = "Change Pin Visibility",
    ):
        super().__init__(description)
        self._scene = scene
        self._block_id = block_id
        self._old_visibility = dict(old_visibility)
        self._new_visibility = dict(new_visibility)

    def undo(self):
        self._apply(self._old_visibility)

    def redo(self):
        self._apply(self._new_visibility)

    def _apply(self, visibility: dict[tuple[str, str], bool]) -> None:
        item = _live_item(self._scene, self._block_id)
        if item is None:
            log.warning(
                "ChangeTerminalVisibilityCommand: block %s is no longer on "
                "the canvas — the pin change could not be applied",
                self._block_id,
            )
            return
        for (direction, name), hidden in visibility.items():
            terminals = (
                item.block.inputs if direction == "input"
                else item.block.outputs
            )
            terminal = terminals.get(name)
            if terminal is not None:
                # A connected pin must remain visible so the wire never ends
                # at an invisible, unselectable attachment point.
                terminal.hidden = bool(hidden and not terminal.connected)
        item.rebuild_terminals()
        _notify(self._scene)


# Rename Block Command
class RenameBlockCommand(QUndoCommand):
    """Undo/redo block instance name change."""

    def __init__(self, scene, block_id: str,
                 old_name: str, new_name: str,
                 description: str = "Rename Block"):
        super().__init__(description)
        self._scene = scene
        self._block_id = block_id
        self._old_name = old_name
        self._new_name = new_name

    def undo(self):
        self._apply(self._old_name)

    def redo(self):
        self._apply(self._new_name)

    def _apply(self, name: str):
        block = self._scene._graph.blocks.get(self._block_id)
        if block is None:
            log.warning("RenameBlockCommand: block %s is no longer in the "
                        "graph — the rename could not be applied",
                        self._block_id)
            return
        block.instance_name = name
        item = _live_item(self._scene, self._block_id)
        if item is not None:
            item.update()
        _notify(self._scene)


# ── Comment commands ────────────────────────────────────────────────────
# Comments carry design rationale ("this override exists because …"), so
# losing one to a stray Delete with no way back is a real loss.  The commands
# hold the live CommentItem: once removed from the scene the Python wrapper is
# its only owner, so the item — text, size and position intact — is exactly
# what comes back on undo.

class AddCommentCommand(QUndoCommand):
    """Undo comment creation by removing it; redo by re-adding the same item."""

    def __init__(self, scene, item, description: str = "Add Comment"):
        super().__init__(description)
        self._scene = scene
        self._item = item
        self._first_redo = True  # already added before the push

    def undo(self):
        self._scene._remove_comment_internal(self._item)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._scene._add_comment_internal(self._item)


class DeleteCommentCommand(QUndoCommand):
    """Undo comment deletion by putting the same item back."""

    def __init__(self, scene, item, description: str = "Delete Comment"):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def undo(self):
        self._scene._add_comment_internal(self._item)

    def redo(self):
        self._scene._remove_comment_internal(self._item)


class EditCommentCommand(QUndoCommand):
    """Undo/redo a comment text edit."""

    def __init__(self, scene, item, old_text: str, new_text: str,
                 description: str = "Edit Comment"):
        super().__init__(description)
        self._scene = scene
        self._item = item
        self._old_text = old_text
        self._new_text = new_text

    def undo(self):
        self._scene._set_comment_text_internal(self._item, self._old_text)

    def redo(self):
        self._scene._set_comment_text_internal(self._item, self._new_text)


# ── Toggle BKCAL Command ────────────────────────────────────────────────

class ToggleBkcalCommand(QUndoCommand):
    """Undo/redo the BKCAL flag on a wire.

    "Mark as BKCAL" is a control-semantics edit, not decoration: the compiler
    splits ``forward_wires`` from ``bkcal_wires`` on this flag, so flipping it
    changes execution order and the back-calculation path.  It used to be
    applied straight on the ``Wire`` object — no undo entry, and the module
    was never marked modified.
    """

    def __init__(self, scene, wire_id: str, old_value: bool, new_value: bool,
                 description: str = "Toggle BKCAL"):
        super().__init__(description)
        self._scene = scene
        self._wire_id = wire_id
        self._old = bool(old_value)
        self._new = bool(new_value)

    def undo(self):
        self._scene._set_wire_bkcal_internal(self._wire_id, self._old)

    def redo(self):
        self._scene._set_wire_bkcal_internal(self._wire_id, self._new)
