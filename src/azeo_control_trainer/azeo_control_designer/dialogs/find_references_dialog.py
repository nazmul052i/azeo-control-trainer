r"""Find References dialog — DCS-standard "where is this block used?"

Walks the parent ``StrategyGraph`` from the selected block and lists
every transitive consumer (downstream) and producer (upstream),
grouped by hop distance. Double-clicking an entry asks the host to
navigate to that block.

Public API
----------
``FindReferencesDialog(block_id, graph, scene=None, parent=None)``
    ``graph`` is the parent :class:`StrategyGraph`. ``scene`` is the
    QGraphicsScene whose ``views()`` can be used to centre on the
    target block — optional but nice-to-have.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


class FindReferencesDialog(QDialog):
    """Two-column tree showing transitive upstream and downstream blocks."""

    blockNavigateRequested = Signal(str)   # block_id to navigate to

    def __init__(self, block_id: str, graph, scene=None, parent=None):
        super().__init__(parent)
        self._block_id = block_id
        self._graph = graph
        self._scene = scene
        blk = graph.blocks.get(block_id)
        title = (f"{blk.instance_name}  ({blk.block_type})"
                 if blk else block_id)
        self.setWindowTitle(f"Find References — {title}")
        self.resize(720, 500)
        self._build(blk)

    def _build(self, blk):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        if blk is not None:
            header = QLabel(
                f"<b>{blk.instance_name}</b> "
                f"&nbsp;·&nbsp; <span style='color:#555;'>"
                f"{blk.block_type}</span><br>"
                "<span style='color:#888; font-size:9pt;'>Every block "
                "transitively reached through this block's outputs is "
                "<i>downstream</i>; everything that feeds its inputs is "
                "<i>upstream</i>. Double-click any row to navigate.</span>")
            header.setStyleSheet(
                f"background: {UI.hover}; padding: 6px 10px;"
                f" border: 1px solid {UI.border};")
            v.addWidget(header)

        split = QSplitter(Qt.Horizontal)

        upstream = self._make_tree("Upstream (sources feeding this block)")
        self._fill_tree(upstream, self._walk(self._block_id, direction="up"))
        split.addWidget(self._wrap_tree("Upstream (sources)", upstream))

        downstream = self._make_tree("Downstream (consumers fed by this block)")
        self._fill_tree(downstream,
                         self._walk(self._block_id, direction="down"))
        split.addWidget(self._wrap_tree("Downstream (consumers)", downstream))

        split.setSizes([360, 360])
        v.addWidget(split, 1)

        bbox = QDialogButtonBox(QDialogButtonBox.Close)
        bbox.rejected.connect(self.reject)
        bbox.accepted.connect(self.accept)
        v.addWidget(bbox)

    def _wrap_tree(self, title: str, tree: QTreeWidget) -> QWidget:
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {UI.blue}; font-weight: bold; padding: 4px 6px;"
            f" background: {UI.hover}; border: 1px solid {UI.border};"
            " border-bottom: none;")
        wl.addWidget(lbl)
        wl.addWidget(tree, 1)
        return wrap

    def _make_tree(self, _title: str) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels(["Block", "Type", "Hops", "via"])
        t.setRootIsDecorated(True)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(
            f"QTreeWidget {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas; font-size: 9pt;"
            " alternate-background-color: #FAFBFD; }"
            f" QTreeWidget::item:hover {{ background: {UI.selection}; }}"
            f" QTreeWidget::item:selected {{ background: {UI.blue}; color: white; }}")
        t.itemDoubleClicked.connect(self._on_navigate)
        return t

    # ----- BFS walk over forward wires -----
    def _walk(self, start: str, *, direction: str) -> list[dict]:
        """Return list of dicts {block_id, block, hops, via_block, via_term}.
        ``direction``='down' follows OUT->IN wires; 'up' follows IN<-OUT wires."""
        if start not in self._graph.blocks:
            return []
        seen: dict[str, dict] = {}
        queue: deque = deque()
        queue.append((start, 0, None, None))
        results: list[dict] = []
        while queue:
            bid, hops, via_id, via_term = queue.popleft()
            if bid in seen:
                continue
            seen[bid] = {"hops": hops, "via": via_id, "term": via_term}
            if bid != start:
                results.append({
                    "block_id": bid,
                    "block": self._graph.blocks[bid],
                    "hops": hops,
                    "via_block": (self._graph.blocks.get(via_id).instance_name
                                   if via_id and via_id in self._graph.blocks
                                   else ""),
                    "via_term": via_term or "",
                })
            for wire in self._graph.wires.values():
                if direction == "down" and wire.src_block_id == bid:
                    queue.append((wire.dst_block_id, hops + 1, bid,
                                   wire.src_terminal))
                elif direction == "up" and wire.dst_block_id == bid:
                    queue.append((wire.src_block_id, hops + 1, bid,
                                   wire.dst_terminal))
        return results

    def _fill_tree(self, tree: QTreeWidget, items: list[dict]) -> None:
        tree.clear()
        if not items:
            empty = QTreeWidgetItem(["(none)", "", "", ""])
            empty.setDisabled(True)
            tree.addTopLevelItem(empty)
            return
        # Group by hop distance for legibility
        by_hops: dict[int, list[dict]] = {}
        for r in items:
            by_hops.setdefault(r["hops"], []).append(r)
        for hops in sorted(by_hops):
            section = QTreeWidgetItem(
                [f"Hop {hops}  ({len(by_hops[hops])})", "", "", ""])
            f = QFont(tree.font())
            f.setBold(True)
            section.setFont(0, f)
            tree.addTopLevelItem(section)
            for r in sorted(by_hops[hops], key=lambda x: x["block"].instance_name):
                leaf = QTreeWidgetItem([
                    r["block"].instance_name,
                    r["block"].block_type,
                    str(r["hops"]),
                    f"{r['via_block']}.{r['via_term']}" if r["via_block"] else "",
                ])
                leaf.setData(0, Qt.UserRole, r["block_id"])
                section.addChild(leaf)
            section.setExpanded(True)
        for i in range(tree.columnCount()):
            tree.resizeColumnToContents(i)

    def _on_navigate(self, item: QTreeWidgetItem, _col: int):
        bid = item.data(0, Qt.UserRole)
        if not bid:
            return
        self.blockNavigateRequested.emit(bid)
        # If a scene was supplied, centre its first view on the target.
        if self._scene is not None:
            from ..items.block_item import BlockItem
            for it in self._scene.items():
                if isinstance(it, BlockItem) and it.block.id == bid:
                    self._scene.clearSelection()
                    it.setSelected(True)
                    views = self._scene.views()
                    if views:
                        views[0].centerOn(it)
                    break
