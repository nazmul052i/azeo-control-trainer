# -*- coding: utf-8 -*-
"""The trend studio: historian-backed trending an operator can work with.

Up to eight pens read back from the SQLite historian. The time axis zooms
under the wheel and pans under the mouse; *Live* keeps the right edge on
now, and any pan or zoom lets go of it. A cursor line reads every pen out
at one moment in engineering units. Pens carry their own colour and scale
(autoscale, the tag's range, or a manual span), pen sets can be saved as
named groups and recalled in one click, and the window exports to CSV
(long format: tag, time, value) or PNG.

The panel keeps the old ``TrendPanel`` surface - ``add_pen``,
``clear_pens``, ``pens``, ``window_box``, ``refresh`` - so the main
window, the variables tree and the saved workspace keep working
unchanged.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                               QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QMenu, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import theme

MAX_PENS = 8
PEN_COLOURS = ["#2033CC", "#C1272D", "#2F6B3A", "#E8960F", "#7A3FA8",
               "#0F7E7E", "#8A5A2B", "#B0179B"]
WINDOWS = [("5 min", 300), ("15 min", 900), ("1 hour", 3600),
           ("4 hours", 14400), ("12 hours", 43200), ("24 hours", 86400)]
MIN_SPAN, MAX_SPAN = 30.0, 7 * 24 * 3600.0
_GROUPS_KEY = "trend_groups"


@dataclass
class TrendPen:
    tag: str
    colour: QColor
    rows: List[Tuple[float, float]] = field(default_factory=list)
    scale_mode: str = "auto"            # auto / range / manual
    lo: float = 0.0
    hi: float = 1.0
    manual_lo: float = 0.0
    manual_hi: float = 100.0

    def value_at(self, t: float) -> Optional[float]:
        """Latest sample at or before t."""
        best = None
        for ts, v in self.rows:
            if ts <= t:
                best = v
            else:
                break
        return best


class TrendCanvas(QWidget):
    """Pens, grid, time axis, cursor. Wheel zooms, drag pans."""

    cursor_moved = Signal(float)        # cursor time, 0.0 = gone
    view_changed = Signal()             # user zoomed or panned

    M_LEFT, M_RIGHT, M_TOP, M_BOT = 8, 8, 8, 22

    def __init__(self) -> None:
        super().__init__()
        self.pens: List[TrendPen] = []
        self.window_s = 900.0
        self.t_end = time.time()
        self.cursor_t = 0.0
        self._drag_x: Optional[float] = None
        self.setMouseTracking(True)
        self.setMinimumHeight(160)

    # ---------------------------------------------------------------- helpers
    def _plot_rect(self):
        return (self.M_LEFT, self.M_TOP,
                self.width() - self.M_LEFT - self.M_RIGHT,
                self.height() - self.M_TOP - self.M_BOT)

    def _t_of_x(self, x: float) -> float:
        px, _py, pw, _ph = self._plot_rect()
        if pw <= 0:
            return self.t_end
        return self.t_end - (1.0 - (x - px) / pw) * self.window_s

    # ----------------------------------------------------------------- events
    def wheelEvent(self, ev) -> None:  # noqa: N802
        anchor = self._t_of_x(ev.position().x())
        factor = 0.8 if ev.angleDelta().y() > 0 else 1.25
        new_span = min(max(self.window_s * factor, MIN_SPAN), MAX_SPAN)
        # keep the time under the wheel where it is
        px, _py, pw, _ph = self._plot_rect()
        frac = (ev.position().x() - px) / pw if pw > 0 else 1.0
        self.t_end = anchor + (1.0 - frac) * new_span
        self.window_s = new_span
        self.view_changed.emit()
        self.update()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._drag_x = ev.position().x()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._drag_x = None

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag_x is not None:
            _px, _py, pw, _ph = self._plot_rect()
            if pw > 0:
                dt = (self._drag_x - ev.position().x()) / pw * self.window_s
                self.t_end += dt
                self._drag_x = ev.position().x()
                self.view_changed.emit()
        self.cursor_t = self._t_of_x(ev.position().x())
        self.cursor_moved.emit(self.cursor_t)
        self.update()

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self.cursor_t = 0.0
        self.cursor_moved.emit(0.0)
        self.update()

    # ---------------------------------------------------------------- painting
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FDFDFD"))
        px, py, pw, ph = self._plot_rect()
        if pw <= 10 or ph <= 10:
            return
        p.setPen(QPen(theme.CANVAS_GRID, 1))
        for i in range(1, 4):
            y = py + ph * i / 4
            p.drawLine(px, y, px + pw, y)

        t0 = self.t_end - self.window_s
        n_ticks = max(2, min(6, pw // 110))
        p.setFont(theme.font(7, mono=True))
        for i in range(int(n_ticks) + 1):
            frac = i / n_ticks
            x = px + pw * frac
            p.setPen(QPen(theme.CANVAS_GRID, 1))
            p.drawLine(x, py, x, py + ph)
            t = t0 + self.window_s * frac
            label = time.strftime("%H:%M:%S", time.localtime(t))
            p.setPen(QPen(theme.MUTED_TEXT, 1))
            p.drawText(QPointF(x - 26, py + ph + 14), label)

        for pen in self.pens:
            if not pen.rows or pen.hi <= pen.lo:
                continue
            p.setPen(QPen(pen.colour, 1.4))
            span = pen.hi - pen.lo
            last = None
            for t, v in pen.rows:
                if t < t0 - 5 or t > self.t_end + 5:
                    last = None if t > self.t_end else last
                    if t < t0 - 5:
                        last = (t, v)
                    continue
                x = px + pw * (1.0 - (self.t_end - t) / self.window_s)
                y = py + ph * (1.0 - (v - pen.lo) / span)
                y = min(max(y, py), py + ph)
                if last is not None:
                    lx = px + pw * (1.0 - (self.t_end - last[0])
                                    / self.window_s)
                    ly = py + ph * (1.0 - (last[1] - pen.lo) / span)
                    ly = min(max(ly, py), py + ph)
                    p.drawLine(QPointF(max(lx, px), ly), QPointF(x, y))
                last = (t, v)

        if self.cursor_t and t0 <= self.cursor_t <= self.t_end:
            x = px + pw * (1.0 - (self.t_end - self.cursor_t)
                           / self.window_s)
            p.setPen(QPen(QColor("#444444"), 1, Qt.DashLine))
            p.drawLine(x, py, x, py + ph)

        p.setPen(QPen(theme.MUTED_TEXT, 1))
        p.drawRect(px, py, pw, ph)


class TrendStudio(QWidget):
    """The full trending panel; also usable as a floating window."""

    COLS = ["", "Tag", "At cursor", "Min", "Max", "Scale"]

    def __init__(self, db, historian, controller=None) -> None:
        super().__init__()
        self.db = db
        self.historian = historian
        self.controller = controller
        self.pens: List[str] = []           # tag order, the public surface
        self._pens: Dict[str, TrendPen] = {}
        self._last_reload = 0.0

        self.canvas = TrendCanvas()
        self.canvas.cursor_moved.connect(self._cursor_moved)
        self.canvas.view_changed.connect(self._user_moved_view)

        self.window_box = QComboBox()
        for label, secs in WINDOWS:
            self.window_box.addItem(label, secs)
        self.window_box.setCurrentIndex(1)
        self.window_box.currentIndexChanged.connect(self._window_selected)

        self.follow = QCheckBox("Live")
        self.follow.setChecked(True)
        self.follow.toggled.connect(lambda _c: self._reload(force=True))

        btn_add = QPushButton("Add pen...")
        btn_add.clicked.connect(self._add_pen_dialog)
        btn_groups = QPushButton("Groups")
        btn_groups.clicked.connect(self._groups_menu)
        btn_csv = QPushButton("CSV")
        btn_csv.setToolTip("Export the visible window, long format")
        btn_csv.clicked.connect(self._export_csv)
        btn_png = QPushButton("PNG")
        btn_png.clicked.connect(self._export_png)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear_pens)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Window"))
        bar.addWidget(self.window_box)
        bar.addWidget(self.follow)
        bar.addStretch(1)
        for b in (btn_add, btn_groups, btn_csv, btn_png, btn_clear):
            bar.addWidget(b)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setFont(theme.font(8, mono=True))
        self.table.setMaximumHeight(150)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._pen_menu)
        self.table.cellDoubleClicked.connect(self._swatch_clicked)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.status = QLabel("-")
        self.status.setFont(theme.font(8))
        self.status.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(bar)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.table)
        lay.addWidget(self.status)

    # ----------------------------------------------------------- pens, public
    def add_pen(self, tag: str) -> None:
        if tag in self._pens or tag not in self.db:
            return
        if len(self.pens) >= MAX_PENS:
            self._remove_pen(self.pens[0])
        colour = QColor(PEN_COLOURS[len(self.pens) % len(PEN_COLOURS)])
        used = {p.colour.name().lower() for p in self._pens.values()}
        for c in PEN_COLOURS:
            if c.lower() not in used:
                colour = QColor(c)
                break
        self.pens.append(tag)
        self._pens[tag] = TrendPen(tag=tag, colour=colour)
        self._reload(force=True)

    def clear_pens(self) -> None:
        self.pens.clear()
        self._pens.clear()
        self._reload(force=True)

    def _remove_pen(self, tag: str) -> None:
        self.pens = [t for t in self.pens if t != tag]
        self._pens.pop(tag, None)
        self._reload(force=True)

    # ------------------------------------------------------------- view state
    def _window_selected(self) -> None:
        self.canvas.window_s = float(self.window_box.currentData())
        self.follow.setChecked(True)
        self._reload(force=True)

    def _user_moved_view(self) -> None:
        # panning or zooming lets go of the right edge
        if self.follow.isChecked():
            self.follow.blockSignals(True)
            self.follow.setChecked(False)
            self.follow.blockSignals(False)
        self._reload(force=True, keep_view=True)

    # ---------------------------------------------------------------- reading
    def refresh(self, _snap=None) -> None:
        if self.follow.isChecked() and time.time() - self._last_reload > 1.0:
            self._reload()

    def _reload(self, force: bool = False, keep_view: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_reload < 1.0:
            return
        self._last_reload = now
        if self.follow.isChecked() and not keep_view:
            self.canvas.t_end = now
        t1 = self.canvas.t_end
        t0 = t1 - self.canvas.window_s
        for tag in self.pens:
            pen = self._pens[tag]
            pen.rows = self.historian.query(tag, t0 - 5, t1 + 5)
            t = self.db.get(tag)
            if pen.scale_mode == "manual":
                pen.lo, pen.hi = pen.manual_lo, pen.manual_hi
            elif pen.scale_mode == "range" and t is not None \
                    and t.kind.analogue:
                pen.lo, pen.hi = float(t.lo), float(t.hi)
            elif pen.rows:
                vals = [v for _, v in pen.rows]
                lo, hi = min(vals), max(vals)
                pad = (hi - lo) * 0.08 or 1.0
                pen.lo, pen.hi = lo - pad, hi + pad
            else:
                pen.lo, pen.hi = 0.0, 1.0
        self.canvas.pens = [self._pens[t] for t in self.pens]
        self.canvas.update()
        self._fill_table()
        st = self.historian.stats
        self.status.setText(
            f"{'live' if self.follow.isChecked() else 'browsing history'} · "
            f"historian {'recording' if st.running else 'stopped'} · "
            f"{st.rows_written:,} rows · "
            f"{self.historian.file_size_mb():.1f} MB")

    # ------------------------------------------------------------- pen table
    def _fill_table(self) -> None:
        cursor = self.canvas.cursor_t
        self.table.setRowCount(len(self.pens))
        for r, tag in enumerate(self.pens):
            pen = self._pens[tag]
            t = self.db.get(tag)
            eu = t.eu if (t is not None and t.kind.analogue) else ""
            at = pen.value_at(cursor) if cursor else (
                pen.rows[-1][1] if pen.rows else None)
            vals = [v for _, v in pen.rows]
            cells = ["■", tag,
                     f"{at:.2f} {eu}" if at is not None else "-",
                     f"{min(vals):.2f}" if vals else "-",
                     f"{max(vals):.2f}" if vals else "-",
                     {"auto": "auto",
                      "range": f"{pen.lo:g}..{pen.hi:g} (range)",
                      "manual": f"{pen.lo:g}..{pen.hi:g} (manual)"
                      }[pen.scale_mode]]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c == 0:
                    item.setForeground(pen.colour)
                    f = item.font(); f.setPointSize(12); item.setFont(f)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def _cursor_moved(self, _t: float) -> None:
        self._fill_table()

    def _selected_tag(self) -> Optional[str]:
        r = self.table.currentRow()
        return self.pens[r] if 0 <= r < len(self.pens) else None

    def _swatch_clicked(self, row: int, col: int) -> None:
        if col != 0 or not (0 <= row < len(self.pens)):
            return
        pen = self._pens[self.pens[row]]
        colour = QColorDialog.getColor(pen.colour, self, f"{pen.tag} colour")
        if colour.isValid():
            pen.colour = colour
            self.canvas.update()
            self._fill_table()

    def _pen_menu(self, pos) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        pen = self._pens[tag]
        menu = QMenu(self)
        for mode, label in (("auto", "Autoscale"),
                            ("range", "Scale to tag range")):
            a = menu.addAction(label,
                               lambda m=mode: self._set_scale(tag, m))
            a.setCheckable(True)
            a.setChecked(pen.scale_mode == mode)
        a = menu.addAction("Set scale...",
                           lambda: self._manual_scale(tag))
        a.setCheckable(True)
        a.setChecked(pen.scale_mode == "manual")
        menu.addSeparator()
        menu.addAction("Colour...", lambda: self._swatch_clicked(
            self.pens.index(tag), 0))
        menu.addAction("Remove pen", lambda: self._remove_pen(tag))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _set_scale(self, tag: str, mode: str) -> None:
        self._pens[tag].scale_mode = mode
        self._reload(force=True, keep_view=True)

    def _manual_scale(self, tag: str) -> None:
        pen = self._pens[tag]
        text, ok = QInputDialog.getText(
            self, f"{tag} scale", "low, high:",
            text=f"{pen.lo:g}, {pen.hi:g}")
        if not ok:
            return
        try:
            lo, hi = (float(x) for x in text.replace(",", " ").split())
            if hi <= lo:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Scale", "Give two numbers, low high.")
            return
        pen.manual_lo, pen.manual_hi, pen.scale_mode = lo, hi, "manual"
        self._reload(force=True, keep_view=True)

    # ------------------------------------------------------------ add / groups
    def _add_pen_dialog(self) -> None:
        from .quick_nav import QuickNav
        QuickNav(self.db, self.controller, self.add_pen, self.add_pen,
                 self).exec()

    def _load_groups(self) -> Dict:
        raw = QSettings("AzeoPlant", "AzeoPlant Simulator").value(
            _GROUPS_KEY, "")
        try:
            groups = json.loads(raw) if raw else {}
            return groups if isinstance(groups, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _store_groups(self, groups: Dict) -> None:
        QSettings("AzeoPlant", "AzeoPlant Simulator").setValue(
            _GROUPS_KEY, json.dumps(groups))

    def _groups_menu(self) -> None:
        groups = self._load_groups()
        menu = QMenu(self)
        for name in sorted(groups):
            menu.addAction(name, lambda n=name: self._apply_group(n))
        if groups:
            menu.addSeparator()
        menu.addAction("Save current as...", self._save_group)
        if groups:
            delete = menu.addMenu("Delete")
            for name in sorted(groups):
                delete.addAction(name, lambda n=name: self._delete_group(n))
        menu.exec(self.mapToGlobal(self.sender().geometry().bottomLeft()))

    def _save_group(self) -> None:
        if not self.pens:
            QMessageBox.information(self, "Trend groups",
                                    "Add pens first, then save the set.")
            return
        name, ok = QInputDialog.getText(self, "Save trend group",
                                        "Group name:")
        name = name.strip()
        if not ok or not name:
            return
        groups = self._load_groups()
        groups[name] = {
            "pens": list(self.pens),
            "colours": {t: self._pens[t].colour.name() for t in self.pens},
            "window": self.canvas.window_s,
        }
        self._store_groups(groups)
        self.status.setText(f"Group '{name}' saved")

    def _apply_group(self, name: str) -> None:
        g = self._load_groups().get(name)
        if not g:
            return
        self.clear_pens()
        for tag in g.get("pens", []):
            self.add_pen(tag)
            colour = g.get("colours", {}).get(tag)
            if colour and tag in self._pens:
                self._pens[tag].colour = QColor(colour)
        window = float(g.get("window", 900.0))
        self.canvas.window_s = window
        for i in range(self.window_box.count()):
            if float(self.window_box.itemData(i)) == window:
                self.window_box.blockSignals(True)
                self.window_box.setCurrentIndex(i)
                self.window_box.blockSignals(False)
                break
        self.follow.setChecked(True)
        self._reload(force=True)

    def _delete_group(self, name: str) -> None:
        groups = self._load_groups()
        groups.pop(name, None)
        self._store_groups(groups)

    # ----------------------------------------------------------------- export
    def _export_csv(self) -> None:
        if not self.pens:
            return
        path, _f = QFileDialog.getSaveFileName(
            self, "Export trend data", "trend.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["tag", "time", "value"])
                for tag in self.pens:
                    for t, v in self._pens[tag].rows:
                        w.writerow([tag, time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(t)), v])
            self.status.setText(f"Exported {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export", str(exc))

    def _export_png(self) -> None:
        path, _f = QFileDialog.getSaveFileName(
            self, "Export trend image", "trend.png", "PNG images (*.png)")
        if path:
            self.canvas.grab().save(path)
            self.status.setText(f"Exported {path}")
