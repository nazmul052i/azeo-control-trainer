# -*- coding: utf-8 -*-
"""Alarm annunciation: the banner and the summary.

The banner is the strip that never goes away: the most important
unacknowledged alarms, newest and worst first, each a button that opens the
faceplate. The summary is the dockable working view - active alarms, the
shelved list and the event journal - where alarms are acknowledged and
shelved in bulk. Both read the plant-wide
:class:`~azeoplant.control.alarms.AlarmSystem`; nothing here scans a tag.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ..control.alarms import ACKED, RTN_UNACK, UNACK, AlarmPoint
from . import theme

_PRI_COLOUR = {"CRITICAL": theme.ALARM_CRITICAL, "HIGH": theme.ALARM_HIGH,
               "ADVISORY": theme.ALARM_ADVISORY}


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


# ===================================================================== banner
class AlarmBanner(QWidget):
    """Always-visible strip of the worst unacknowledged alarms."""

    SLOTS = 5

    def __init__(self, alarms, open_faceplate: Callable[[str], None],
                 open_summary: Callable[[], None]) -> None:
        super().__init__()
        self.alarms = alarms
        self.open_faceplate = open_faceplate
        self.max_slots = self.SLOTS
        self.setFixedHeight(30)
        self.setObjectName("alarmBanner")
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(4)

        self.count = QLabel("-")
        self.count.setFont(theme.font(8, bold=True, mono=True))
        self.count.setMinimumWidth(140)
        lay.addWidget(self.count)

        self._slots: List[QPushButton] = []
        for _ in range(self.SLOTS):
            b = QPushButton("")
            b.setFont(theme.font(8, bold=True))
            b.setVisible(False)
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, btn=b: self._slot_menu(btn, pos))
            b.clicked.connect(lambda _c, btn=b: self._slot_open(btn))
            lay.addWidget(b, 1)
            self._slots.append(b)
        lay.addStretch(1)

        self.btn_silence = QPushButton("Silence")
        self.btn_silence.setToolTip("Stop the audible annunciation; the "
                                    "alarms stay unacknowledged")
        self.btn_silence.clicked.connect(self.alarms.silence)
        self.btn_ack = QPushButton("Ack shown")
        self.btn_ack.setToolTip("Acknowledge every alarm on the banner")
        self.btn_ack.clicked.connect(self._ack_shown)
        self.btn_summary = QPushButton("Alarms...")
        self.btn_summary.clicked.connect(open_summary)
        for b in (self.btn_silence, self.btn_ack, self.btn_summary):
            b.setFont(theme.font(8))
            lay.addWidget(b)

    # ---------------------------------------------------------------- refresh
    def refresh(self) -> None:
        rows = self.alarms.banner(min(self.max_slots, self.SLOTS))
        flash_on = (time.time() % 1.0) < 0.5
        for i, b in enumerate(self._slots):
            if i >= len(rows):
                b.setVisible(False)
                b.setProperty("alarm_key", None)
                continue
            p = rows[i]
            b.setVisible(True)
            b.setProperty("alarm_key", p.key)
            label = f"{_clock(p.since)}  {p.tag}  {p.atype}"
            if p.first_out:
                label += "  FIRST OUT"
            b.setText(label)
            colour = _PRI_COLOUR.get(p.priority, theme.MUTED_TEXT)
            if p.state == UNACK and flash_on:
                style = (f"background: {colour.name()}; color: white;"
                         f" border: 1px solid {colour.darker(130).name()};")
            elif p.state == UNACK:
                style = (f"background: {colour.lighter(160).name()};"
                         f" color: #1A1A1A;"
                         f" border: 1px solid {colour.name()};")
            else:                                     # cleared, awaiting ack
                style = (f"background: #DDDDDD; color: {colour.name()};"
                         f" border: 1px dashed {colour.name()};")
            b.setStyleSheet("QPushButton { " + style + " padding: 2px 6px; }")

        c = self.alarms.counts()
        unack = self.alarms.unacked_count()

        def chip(n, letter, col):
            weight = 700 if n else 400
            colour = col.name() if n else theme.MUTED_TEXT.name()
            return (f"<span style='color:{colour};"
                    f"font-weight:{weight}'>{n}{letter}</span>")

        self.count.setText(
            chip(c["CRITICAL"], "C", theme.ALARM_CRITICAL) + " "
            + chip(c["HIGH"], "H", theme.ALARM_HIGH) + " "
            + chip(c["ADVISORY"], "A", theme.ALARM_ADVISORY)
            + f" &nbsp;<span style='color:{theme.MUTED_TEXT.name()}'>"
            f"· {unack} unack</span>")
        self.btn_ack.setEnabled(bool(rows))

    # ---------------------------------------------------------------- actions
    def _point(self, btn) -> Optional[AlarmPoint]:
        key = btn.property("alarm_key")
        if not key:
            return None
        return next((p for p in self.alarms.points if p.key == key), None)

    def _slot_open(self, btn) -> None:
        p = self._point(btn)
        if p is not None:
            self.open_faceplate(p.tag)

    def _slot_menu(self, btn, pos) -> None:
        p = self._point(btn)
        if p is None:
            return
        menu = QMenu(self)
        menu.addAction("Acknowledge", lambda: self.alarms.ack(p))
        menu.addAction("Shelve 30 min",
                       lambda: self.alarms.shelve(p, 30.0))
        menu.addSeparator()
        menu.addAction("Open faceplate", lambda: self.open_faceplate(p.tag))
        menu.exec(btn.mapToGlobal(pos))

    def _ack_shown(self) -> None:
        self.alarms.ack_all(self.alarms.banner(
            min(self.max_slots, self.SLOTS)))


# ==================================================================== summary
_ACTIVE_COLS = ["", "Time", "Tag", "Type", "Priority", "Value", "Limit",
                "Unit", "Description"]
_JOURNAL_COLS = ["Time", "Event", "Priority", "Tag", "Message", "Value", "By"]
_JOURNAL_WINDOWS = [("1 hour", 3600), ("8 hours", 8 * 3600),
                    ("24 hours", 24 * 3600), ("7 days", 7 * 24 * 3600)]


class AlarmSummaryPanel(QWidget):
    """Active alarms, the shelved list, and the journal, with ack and shelve."""

    def __init__(self, alarms, journal,
                 open_faceplate: Callable[[str], None]) -> None:
        super().__init__()
        self.alarms = alarms
        self.journal = journal
        self.open_faceplate = open_faceplate
        self._journal_at = 0.0

        # ------------------------------------------------------------- filters
        self.pri_box = QComboBox()
        self.pri_box.addItems(["All priorities", "CRITICAL", "HIGH",
                               "ADVISORY"])
        self.unit_box = QComboBox()
        self.unit_box.addItem("All units")
        self.text_filter = QLineEdit()
        self.text_filter.setPlaceholderText("filter tag or description")
        self.text_filter.setClearButtonEnabled(True)

        btn_ack = QPushButton("Ack selected")
        btn_ack.clicked.connect(self._ack_selected)
        btn_ack_all = QPushButton("Ack all")
        btn_ack_all.clicked.connect(lambda: self.alarms.ack_all())
        btn_shelve = QPushButton("Shelve...")
        btn_shelve.clicked.connect(self._shelve_selected)
        btn_unshelve = QPushButton("Unshelve")
        btn_unshelve.clicked.connect(self._unshelve_selected)

        bar = QHBoxLayout()
        for w in (self.pri_box, self.unit_box, self.text_filter):
            bar.addWidget(w)
        bar.addStretch(1)
        for b in (btn_ack, btn_ack_all, btn_shelve, btn_unshelve):
            bar.addWidget(b)

        # -------------------------------------------------------------- tables
        self.active_table = self._make_table(_ACTIVE_COLS)
        self.active_table.doubleClicked.connect(self._open_selected)
        self.active_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.active_table.customContextMenuRequested.connect(self._menu)
        self.shelved_table = self._make_table(_ACTIVE_COLS)

        journal_page = QWidget()
        jbar = QHBoxLayout()
        self.cat_box = QComboBox()
        self.cat_box.addItems(["All events", "ALARM", "RTN", "ACK", "SHELVE",
                               "UNSHELVE", "OOS", "RTS", "TRIP", "TRIP_RESET",
                               "OPERATOR", "SYSTEM"])
        self.window_box = QComboBox()
        for label, secs in _JOURNAL_WINDOWS:
            self.window_box.addItem(label, secs)
        self.window_box.setCurrentIndex(2)
        self.journal_filter = QLineEdit()
        self.journal_filter.setPlaceholderText("filter tag or message")
        self.journal_filter.setClearButtonEnabled(True)
        jrefresh = QPushButton("Refresh")
        jrefresh.clicked.connect(self._reload_journal)
        for w in (self.cat_box, self.window_box, self.journal_filter):
            jbar.addWidget(w)
        jbar.addStretch(1)
        jbar.addWidget(jrefresh)
        self.journal_table = self._make_table(_JOURNAL_COLS)
        jlay = QVBoxLayout(journal_page)
        jlay.setContentsMargins(0, 4, 0, 0)
        jlay.addLayout(jbar)
        jlay.addWidget(self.journal_table)
        self.cat_box.currentIndexChanged.connect(self._reload_journal)
        self.window_box.currentIndexChanged.connect(self._reload_journal)
        self.journal_filter.returnPressed.connect(self._reload_journal)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.active_table, "Active")
        self.tabs.addTab(self.shelved_table, "Shelved / OOS")
        self.tabs.addTab(journal_page, "Journal")

        self.info = QLabel("")
        self.info.setFont(theme.font(8))
        self.info.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(bar)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self.info)

    def _make_table(self, cols: List[str]) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setFont(theme.font(8, mono=True))
        t.horizontalHeader().setStretchLastSection(True)
        t.setSortingEnabled(False)
        return t

    # ---------------------------------------------------------------- refresh
    def refresh(self, _snap=None) -> None:
        units = sorted({p.unit for p in self.alarms.points if p.unit})
        if self.unit_box.count() != len(units) + 1:
            current = self.unit_box.currentText()
            self.unit_box.blockSignals(True)
            self.unit_box.clear()
            self.unit_box.addItem("All units")
            self.unit_box.addItems(units)
            ix = self.unit_box.findText(current)
            self.unit_box.setCurrentIndex(max(0, ix))
            self.unit_box.blockSignals(False)

        self._fill(self.active_table, self._filtered(self.alarms.standing()))
        self._fill(self.shelved_table,
                   self.alarms.shelved_points() + self.alarms.oos_points())
        c = self.alarms.counts()
        self.info.setText(
            f"{c['CRITICAL']} critical · {c['HIGH']} high · "
            f"{c['ADVISORY']} advisory active · "
            f"{self.alarms.unacked_count()} unacknowledged · "
            f"{len(self.alarms.shelved_points())} shelved · "
            f"{len(self.alarms.points)} points scanned")
        if (self.tabs.currentIndex() == 2
                and time.time() - self._journal_at > 1.0):
            self._reload_journal()

    def _filtered(self, rows: List[AlarmPoint]) -> List[AlarmPoint]:
        pri = self.pri_box.currentText()
        unit = self.unit_box.currentText()
        text = self.text_filter.text().strip().upper()
        out = []
        for p in rows:
            if pri != "All priorities" and p.priority != pri:
                continue
            if unit != "All units" and p.unit != unit:
                continue
            if text and text not in p.tag.upper() \
                    and text not in p.desc.upper():
                continue
            out.append(p)
        return out

    def _fill(self, table: QTableWidget, rows: List[AlarmPoint]) -> None:
        table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            t = self.alarms.db.get(p.tag)
            value = t.format() if t is not None else p.value_text
            state = ("OOS" if not p.enabled else
                     {UNACK: "●", ACKED: "○", RTN_UNACK: "↓"}.get(p.state, ""))
            limit = (f"{p.setpoint:g} {p.eu}".strip()
                     if p.atype not in ("PV_BAD", "TRIP", "DISCRETE") else "")
            cells = [state, _clock(p.since) if p.since else "",
                     p.tag, p.atype, p.priority, value, limit, p.unit,
                     p.desc + ("   [FIRST OUT]" if p.first_out else "")]
            colour = _PRI_COLOUR.get(p.priority, theme.NORMAL_TEXT)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, p.key)
                if p.state == UNACK:
                    item.setBackground(QBrush(QColor(colour).lighter(170)))
                    f = item.font(); f.setBold(True); item.setFont(f)
                item.setForeground(QBrush(
                    colour if p.active else theme.MUTED_TEXT))
                table.setItem(r, c, item)
        table.resizeColumnsToContents()

    # ---------------------------------------------------------------- journal
    def _reload_journal(self) -> None:
        self._journal_at = time.time()
        if self.journal is None:
            self.journal_table.setRowCount(0)
            return
        cat = self.cat_box.currentText()
        rows = self.journal.query(
            t0=time.time() - float(self.window_box.currentData()),
            category=None if cat == "All events" else cat,
            like=self.journal_filter.text().strip(), limit=500)
        self.journal_table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            colour = _PRI_COLOUR.get(e.priority)
            cells = [time.strftime("%m-%d %H:%M:%S", time.localtime(e.t)),
                     e.category, e.priority, e.tag, e.message, e.value,
                     e.source]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if colour is not None:
                    item.setForeground(QBrush(colour))
                self.journal_table.setItem(r, c, item)
        self.journal_table.resizeColumnsToContents()

    # ---------------------------------------------------------------- actions
    def _selected(self) -> List[AlarmPoint]:
        table = (self.shelved_table if self.tabs.currentIndex() == 1
                 else self.active_table)
        keys = {table.item(ix.row(), 0).data(Qt.UserRole)
                for ix in table.selectedIndexes()
                if table.item(ix.row(), 0) is not None}
        return [p for p in self.alarms.points if p.key in keys]

    def _ack_selected(self) -> None:
        self.alarms.ack_all(self._selected() or None)
        self.refresh()

    def _shelve_selected(self) -> None:
        points = self._selected()
        if not points:
            return
        minutes, ok = QInputDialog.getDouble(
            self, "Shelve alarms",
            f"Shelve {len(points)} alarm(s) for how many minutes?",
            30.0, 1.0, 24 * 60.0, 0)
        if ok:
            for p in points:
                self.alarms.shelve(p, minutes)
            self.refresh()

    def _unshelve_selected(self) -> None:
        for p in self._selected():
            self.alarms.unshelve(p)
        self.refresh()

    def _open_selected(self) -> None:
        points = self._selected()
        if points:
            self.open_faceplate(points[0].tag)

    def _menu(self, pos) -> None:
        points = self._selected()
        if not points:
            return
        p = points[0]
        menu = QMenu(self)
        menu.addAction("Acknowledge",
                       lambda: (self.alarms.ack_all(points), self.refresh()))
        menu.addAction("Shelve...", self._shelve_selected)
        if any(q.enabled for q in points):
            menu.addAction("Remove from service", lambda: self._oos(points))
        if any(not q.enabled for q in points):
            menu.addAction("Return to service", lambda: self._rts(points))
        menu.addSeparator()
        menu.addAction("Open faceplate", lambda: self.open_faceplate(p.tag))
        menu.exec(self.active_table.viewport().mapToGlobal(pos))

    def _oos(self, points: List[AlarmPoint]) -> None:
        for q in points:
            self.alarms.remove_from_service(q)
        self.refresh()

    def _rts(self, points: List[AlarmPoint]) -> None:
        for q in points:
            self.alarms.return_to_service(q)
        self.refresh()
