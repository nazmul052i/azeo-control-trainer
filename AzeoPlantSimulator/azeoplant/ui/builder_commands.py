"""Undo machinery for the display builder.

Every mutation of a builder page is one QUndoCommand operating on
record dicts - the same shape the page serializes to - resolved to
live items through the builder's id registry at execution time, so a
command stays valid across the item rebuilds that property edits
trigger. Hosts are always restored before the connectors that
reference them.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QImage, QPainter, QUndoCommand

_MERGE_NUDGE = 1001


def copy_rec(rec: dict) -> dict:
    out = dict(rec)
    out["props"] = dict(rec.get("props", {}))
    return out


def conn_rank(rec: dict) -> int:
    """Restore order: symbols, then connectors, then the branches that
    tap other connectors (which must already exist)."""
    if rec.get("type") != "connector":
        return 0
    pr = rec.get("props", {})
    return 2 if (pr.get("bt") is not None
                 or pr.get("at") is not None) else 1


def hosts_first(recs: List[dict]) -> List[dict]:
    return sorted(recs, key=conn_rank)


# ----------------------------------------------------------------- clipboard
def clone_recs(recs: List[dict], fresh_id, dx: float = 0.0,
               dy: float = 0.0) -> List[dict]:
    """Deep-copy records with fresh ids from ``fresh_id()``, offset by
    (dx, dy); connector references are remapped within the set and
    connectors pointing outside it are dropped."""
    idmap: Dict[int, int] = {}
    out: List[dict] = []
    for r in hosts_first(recs):
        c = copy_rec(r)
        if c["type"] == "connector":
            fr = idmap.get(c["props"].get("from_id"))
            to = idmap.get(c["props"].get("to_id"))
            if fr is None or to is None:
                continue
            c["props"]["from_id"], c["props"]["to_id"] = fr, to
        else:
            c["x"], c["y"] = c["x"] + dx, c["y"] + dy
        idmap[r["id"]] = c["id"] = fresh_id()
        out.append(c)
    return out


# ------------------------------------------------------------ align/distribute
def align_moves(entries: List[Tuple[int, Tuple[float, float], "QRectF"]],
                mode: str) -> List[Tuple[int, Tuple[float, float],
                                         Tuple[float, float]]]:
    """Moves that align or distribute items. ``entries`` are
    (id, (x, y) position, scene bounding rect); extents come from the
    rects, the delta is applied to the position."""
    if len(entries) < (3 if mode.startswith("dist") else 2):
        return []
    moves = []
    if mode.startswith("dist"):
        horiz = mode == "dist_h"
        key = ((lambda e: e[2].center().x()) if horiz
               else (lambda e: e[2].center().y()))
        ordered = sorted(entries, key=key)
        first, last = key(ordered[0]), key(ordered[-1])
        step = (last - first) / (len(ordered) - 1)
        for n, e in enumerate(ordered[1:-1], start=1):
            d = first + n * step - key(e)
            i, (x, y), _r = e
            new = (x + d, y) if horiz else (x, y + d)
            if abs(d) > 1e-9:
                moves.append((i, (x, y), new))
        return moves
    refs = {"left": min(r.left() for _i, _p, r in entries),
            "right": max(r.right() for _i, _p, r in entries),
            "top": min(r.top() for _i, _p, r in entries),
            "bottom": max(r.bottom() for _i, _p, r in entries),
            "hcenter": sum(r.center().x() for _i, _p, r in entries)
            / len(entries),
            "vcenter": sum(r.center().y() for _i, _p, r in entries)
            / len(entries)}
    ref = refs[mode]
    for i, (x, y), r in entries:
        cur = {"left": r.left(), "right": r.right(), "top": r.top(),
               "bottom": r.bottom(), "hcenter": r.center().x(),
               "vcenter": r.center().y()}[mode]
        d = ref - cur
        if abs(d) < 1e-9:
            continue
        new = (x + d, y) if mode in ("left", "right", "hcenter") \
            else (x, y + d)
        moves.append((i, (x, y), new))
    return moves


# -------------------------------------------------------------------- export
def _render_clean(scene, painter, target: QRectF, source: QRectF) -> None:
    scene.export_mode = True
    try:
        scene.clearSelection()
        scene.render(painter, target, source)
    finally:
        scene.export_mode = False


def export_png(scene, path, scale: float = 2.0) -> None:
    """The page as a raster image at ``scale``x, editing aids omitted."""
    rect = scene.sceneRect()
    img = QImage(int(rect.width() * scale), int(rect.height() * scale),
                 QImage.Format_ARGB32)
    img.fill(scene.backgroundBrush().color())
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _render_clean(scene, p, QRectF(img.rect()), rect)
    p.end()
    img.save(str(path))


def export_svg(scene, path) -> None:
    """The page as self-contained vector artwork."""
    from PySide6.QtSvg import QSvgGenerator
    rect = scene.sceneRect()
    gen = QSvgGenerator()
    gen.setFileName(str(path))
    gen.setSize(QSize(int(rect.width()), int(rect.height())))
    gen.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
    gen.setTitle(getattr(scene, "title", "display"))
    p = QPainter(gen)
    _render_clean(scene, p, QRectF(0, 0, rect.width(), rect.height()), rect)
    p.end()


class AddCmd(QUndoCommand):
    """Place one or more records (a paste or duplicate is one command)."""

    def __init__(self, builder, recs: List[dict], text: str = "Add") -> None:
        super().__init__(text)
        self.b = builder
        self.recs = [copy_rec(r) for r in hosts_first(recs)]

    def redo(self) -> None:
        self.b._restore_many([copy_rec(r) for r in self.recs])

    def undo(self) -> None:
        for r in reversed(self.recs):
            self.b._delete_by_id(r["id"])


class RemoveCmd(QUndoCommand):
    """Delete records by id; the attached-connector cascade is captured
    on the first redo so undo can restore everything."""

    def __init__(self, builder, ids: List[int],
                 text: str = "Delete") -> None:
        super().__init__(text)
        self.b = builder
        self.ids = list(ids)
        self.recs: Optional[List[dict]] = None

    def redo(self) -> None:
        removed: List[dict] = []
        for i in self.ids:
            removed += self.b._delete_by_id(i)
        if self.recs is None:
            self.recs = [copy_rec(r) for r in hosts_first(removed)]
        else:
            for r in self.recs:
                self.b._delete_by_id(r["id"])

    def undo(self) -> None:
        self.b._restore_many([copy_rec(r) for r in self.recs or []])


class MoveCmd(QUndoCommand):
    """Reposition items - a drag, a nudge or an alignment; positions
    apply directly, no rebuild. Nudges merge into one step."""

    def __init__(self, builder,
                 moves: List[Tuple[int, Tuple[float, float],
                                   Tuple[float, float]]],
                 text: str = "Move", mergeable: bool = False) -> None:
        super().__init__(text)
        self.b = builder
        self.moves = list(moves)
        self.mergeable = mergeable

    def id(self) -> int:  # noqa: A003
        return _MERGE_NUDGE if self.mergeable else -1

    def mergeWith(self, other) -> bool:  # noqa: N802
        if not isinstance(other, MoveCmd) or not other.mergeable:
            return False
        news = {i: new for i, _old, new in other.moves}
        self.moves = [(i, old, news.get(i, new))
                      for i, old, new in self.moves]
        for i, old, new in other.moves:
            if i not in {m[0] for m in self.moves}:
                self.moves.append((i, old, new))
        return True

    def _apply(self, to_new: bool) -> None:
        for i, old, new in self.moves:
            x, y = new if to_new else old
            rec = self.b._recs.get(i)
            if rec is not None:
                rec["x"], rec["y"] = x, y
            it = self.b._ids.get(i)
            if it is not None:
                it.setPos(x, y)

    def redo(self) -> None:
        self._apply(True)

    def undo(self) -> None:
        self._apply(False)


class EditCmd(QUndoCommand):
    """Change an item's properties, rotation, mirror or position; the
    item (and any attached connectors) rebuilds through the factories.
    ``old`` and ``new`` are sparse deltas covering the same keys:
    optionally 'props' (a sub-dict), 'rot', 'flip', 'x', 'y'."""

    def __init__(self, builder, item_id: int, old: dict, new: dict,
                 text: str = "Edit") -> None:
        super().__init__(text)
        self.b = builder
        self.item_id = item_id
        self.old = copy_rec(old) if "props" in old else dict(old)
        self.new = copy_rec(new) if "props" in new else dict(new)

    def _apply(self, delta: dict) -> None:
        rec = self.b._recs.get(self.item_id)
        if rec is None:
            return
        rec["props"].update(delta.get("props", {}))
        for k in ("rot", "flip", "x", "y", "lock_aspect"):
            if k in delta:
                rec[k] = delta[k]
        self.b._rebuild_item(self.item_id)
        it = self.b._ids.get(self.item_id)
        if it is not None:
            self.b.scene.clearSelection()
            it.setSelected(True)

    def redo(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)


class ZCmd(QUndoCommand):
    """Restack items; z applies directly, no rebuild."""

    def __init__(self, builder,
                 changes: List[Tuple[int, float, float]],
                 text: str = "Arrange") -> None:
        super().__init__(text)
        self.b = builder
        self.changes = list(changes)

    def _apply(self, to_new: bool) -> None:
        for i, z0, z1 in self.changes:
            z = z1 if to_new else z0
            rec = self.b._recs.get(i)
            if rec is not None:
                rec["z"] = z
            it = self.b._ids.get(i)
            if it is not None:
                it.setZValue(z)

    def redo(self) -> None:
        self._apply(True)

    def undo(self) -> None:
        self._apply(False)
