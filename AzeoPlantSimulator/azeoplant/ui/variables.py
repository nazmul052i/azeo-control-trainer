"""Variables by unit, and history-backed trends.

These two views are the simulator's interface. There is deliberately no mimic
display: the plant is open loop, the DCS owns the graphics, and what an
engineer needs here is the value of every variable and the shape of how it
moved.

``VariablesPanel`` groups the tag database by process unit, because that is how
the plant is built and how the tag schedule is organised. It also carries the
local override, which is what makes the simulator usable with no DCS attached:
an ``AO`` or ``DO`` can be driven by hand and the models obey it.

``TrendPanel`` reads from the SQLite historian rather than from memory, so a
trend can be pulled up over hours, survives a restart, and shows the same
numbers that a query against the file would give.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core.tags import Quality, TagDatabase, TagKind
from . import theme

log = logging.getLogger(__name__)

PEN_COLOURS = ["#1F4E79", "#9E2A2B", "#2F6B3A", "#7A3FA8", "#B5651D", "#0F6E6E"]


# --------------------------------------------------------------- variables
class VariablesPanel(QWidget):
    """Every tag, grouped by the unit that owns it."""

    tag_activated = Signal(str)
    pen_requested = Signal(str)

    COLUMNS = ["Tag", "Description", "Value", "EU", "Quality", "Type", "NodeId"]

    def __init__(self, db: TagDatabase) -> None:
        super().__init__()
        self.db = db

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by tag or description")
        self.filter.textChanged.connect(self._rebuild)
        self.kind_box = QComboBox()
        self.kind_box.addItem("All types", "")
        for k in TagKind:
            self.kind_box.addItem(k.value, k.value)
        self.kind_box.currentIndexChanged.connect(self._rebuild)
        self.writable_only = QCheckBox("DCS writable only")
        self.writable_only.toggled.connect(self._rebuild)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setFont(theme.font(9))
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        #: right-click menu entries provider, set by the main window
        self.context_provider = None

        expand = QPushButton("Expand all")
        expand.clicked.connect(self.tree.expandAll)
        collapse = QPushButton("Collapse all")
        collapse.clicked.connect(self.tree.collapseAll)
        trend = QPushButton("Trend selected")
        trend.clicked.connect(self._trend_selected)

        top = QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.kind_box)
        top.addWidget(self.writable_only)
        self.count_label = QLabel("-")
        bar = QHBoxLayout()
        bar.addWidget(self.count_label, 1)
        bar.addWidget(trend)
        bar.addWidget(expand)
        bar.addWidget(collapse)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bar)

        self._items: Dict[str, QTreeWidgetItem] = {}
        self._rebuild()

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or item.parent() is None:
            return
        tag = item.text(0)
        if self.context_provider is None or tag not in self.db:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for entry in self.context_provider(tag):
            if entry is None:
                menu.addSeparator()
                continue
            text, cb, enabled = entry
            act = menu.addAction(text)
            act.setEnabled(bool(enabled))
            act.triggered.connect(lambda _c=False, f=cb: f())
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ build
    def _rebuild(self) -> None:
        text = self.filter.text().strip().lower()
        kind = self.kind_box.currentData()
        writable = self.writable_only.isChecked()

        self.tree.clear()
        self._items.clear()
        shown = 0
        for unit in self.db.units():
            tags = []
            for t in self.db.by_unit(unit):
                if kind and t.kind.value != kind:
                    continue
                if writable and not t.kind.dcs_writable:
                    continue
                if text and text not in t.name.lower() and text not in t.desc.lower():
                    continue
                tags.append(t)
            if not tags:
                continue
            head = QTreeWidgetItem(self.tree, [f"{unit}  ({len(tags)})"])
            head.setFirstColumnSpanned(True)
            head.setFont(0, theme.font(10, True))
            head.setExpanded(True)
            for t in tags:
                row = QTreeWidgetItem(head, [
                    t.name, t.desc, "", t.eu, "", t.kind.value,
                    f"ns=2;s={t.name}"])
                row.setFont(0, theme.font(9, True, mono=True))
                row.setFont(2, theme.font(9, True, mono=True))
                row.setData(0, Qt.UserRole, t.name)
                self._items[t.name] = row
                shown += 1
        self.count_label.setText(f"{shown} variables in {self.tree.topLevelItemCount()} units")

    # ---------------------------------------------------------------- actions
    def _selected_tag(self) -> Optional[str]:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _activated(self, item: QTreeWidgetItem, _col: int) -> None:
        name = item.data(0, Qt.UserRole)
        if name:
            self.tag_activated.emit(name)

    def _trend_selected(self) -> None:
        name = self._selected_tag()
        if name:
            self.pen_requested.emit(name)

    # ---------------------------------------------------------------- refresh
    def refresh(self, snap: Dict) -> None:
        for name, item in self._items.items():
            entry = snap.get(name)
            if entry is None:
                continue
            value, quality, _ts = entry
            tag = self.db.get(name)
            if isinstance(value, bool):
                text = (tag.state1 if value else tag.state0) if tag else str(value)
            else:
                text = tag.format() if tag else f"{float(value):.2f}"
                text = text.replace(f" {tag.eu}", "") if tag and tag.eu else text
            item.setText(2, text)
            q = Quality(int(quality))
            item.setText(4, q.label)
            colour = theme.quality_colour(int(quality))
            item.setForeground(2, QBrush(colour))
            item.setForeground(4, QBrush(colour))
            if tag is not None and tag.override and tag.kind.dcs_writable:
                item.setForeground(0, QBrush(theme.OVERRIDE))
                item.setText(5, f"{tag.kind.value}  forced")
            else:
                item.setForeground(0, QBrush(theme.NORMAL_TEXT))
                item.setText(5, tag.kind.value if tag else "")


# ------------------------------------------------------------------- trends
class TrendCanvas(QWidget):
    """Strip chart drawn from historian rows, autoscaled per pen."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(260)
        self.series: List[Tuple[str, List[Tuple[float, float]], Tuple[float, float]]] = []
        self.window_s = 600.0
        self.t_end = time.time()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(64, 14, -14, -30)
        p.fillRect(self.rect(), QBrush(theme.PANEL))
        p.fillRect(r, QBrush(QColor("#F8F8F8")))

        p.setPen(QPen(QColor("#C8C8C8"), 1))
        for i in range(1, 5):
            y = r.top() + r.height() * i / 5
            p.drawLine(r.left(), int(y), r.right(), int(y))
        for i in range(1, 6):
            x = r.left() + r.width() * i / 6
            p.drawLine(int(x), r.top(), int(x), r.bottom())
        p.setPen(QPen(QColor("#8A8A8A"), 1))
        p.drawRect(r)

        if not self.series:
            p.setPen(QPen(theme.MUTED_TEXT))
            p.setFont(theme.font(10))
            p.drawText(r, Qt.AlignCenter,
                       "Select variables and press Trend selected")
            return

        t0 = self.t_end - self.window_s
        p.setFont(theme.font(8))
        for idx, (tag, rows, span) in enumerate(self.series):
            colour = QColor(PEN_COLOURS[idx % len(PEN_COLOURS)])
            if len(rows) < 1:
                continue
            lo, hi = span
            if hi - lo < 1e-9:
                lo, hi = lo - 1.0, hi + 1.0
            pad = (hi - lo) * 0.06
            lo, hi = lo - pad, hi + pad

            p.setPen(QPen(colour, 1.8))
            prev = None
            for ts, v in rows:
                x = r.left() + (max(ts, t0) - t0) / self.window_s * r.width()
                y = r.bottom() - (v - lo) / (hi - lo) * r.height()
                if prev is not None:
                    p.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)
            if prev is not None:                     # carry the last value forward
                p.drawLine(int(prev[0]), int(prev[1]), r.right(), int(prev[1]))

            last = rows[-1][1]
            p.setPen(QPen(colour))
            p.drawText(QRectF(r.left() + 6, r.top() + 4 + idx * 14, 320, 13),
                       Qt.AlignLeft, f"{tag}   {last:,.2f}   [{lo:,.1f} .. {hi:,.1f}]")

        p.setPen(QPen(theme.MUTED_TEXT))
        p.setFont(theme.font(8))
        for i in range(7):
            x = r.left() + r.width() * i / 6
            secs = self.window_s * (1 - i / 6)
            label = "now" if i == 6 else f"-{int(secs / 60)} min"
            p.drawText(QRectF(x - 34, r.bottom() + 4, 68, 14), Qt.AlignCenter, label)


class TrendPanel(QWidget):
    """Up to six pens, read back from the historian."""

    MAX_PENS = 6
    WINDOWS = [("5 min", 300), ("15 min", 900), ("1 hour", 3600),
               ("4 hours", 14400), ("12 hours", 43200), ("24 hours", 86400)]

    def __init__(self, db: TagDatabase, historian) -> None:
        super().__init__()
        self.db = db
        self.historian = historian
        self.pens: List[str] = []

        self.canvas = TrendCanvas()
        self.window_box = QComboBox()
        for label, secs in self.WINDOWS:
            self.window_box.addItem(label, secs)
        self.window_box.setCurrentIndex(1)
        self.window_box.currentIndexChanged.connect(self._reload)

        self.pen_list = QLabel("no pens")
        self.pen_list.setFont(theme.font(9, mono=True))
        clear = QPushButton("Clear pens")
        clear.clicked.connect(self.clear_pens)
        self.status = QLabel("-")
        self.status.setFont(theme.font(8))
        self.status.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Window"))
        bar.addWidget(self.window_box)
        bar.addWidget(self.pen_list, 1)
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status)

    # ------------------------------------------------------------------- pens
    def add_pen(self, tag: str) -> None:
        if tag in self.pens:
            return
        if len(self.pens) >= self.MAX_PENS:
            self.pens.pop(0)
        self.pens.append(tag)
        self._update_pen_label()
        self._reload()

    def clear_pens(self) -> None:
        self.pens.clear()
        self._update_pen_label()
        self._reload()

    def _update_pen_label(self) -> None:
        self.pen_list.setText("   ".join(self.pens) if self.pens else "no pens")

    # ---------------------------------------------------------------- reading
    def _reload(self) -> None:
        window = float(self.window_box.currentData())
        t1 = time.time()
        t0 = t1 - window
        series = []
        for tag in self.pens:
            rows = self.historian.query(tag, t0, t1)
            t = self.db.get(tag)
            if t is not None and t.kind.analogue:
                lo, hi = float(t.lo), float(t.hi)
                if rows:
                    lo = min(lo, min(v for _, v in rows))
                    hi = max(hi, max(v for _, v in rows))
            elif rows:
                lo, hi = 0.0, 1.0
            else:
                lo, hi = 0.0, 1.0
            series.append((tag, rows, (lo, hi)))
        self.canvas.series = series
        self.canvas.window_s = window
        self.canvas.t_end = t1
        self.canvas.update()

        st = self.historian.stats
        self.status.setText(
            f"historian {'recording' if st.running else 'stopped'} · "
            f"{st.rows_written:,} rows written · {self.historian.file_size_mb():.1f} MB · "
            f"{self.historian.path}")

    def refresh(self, _snap: Dict) -> None:
        self._reload()
