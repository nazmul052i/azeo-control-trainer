# -*- coding: utf-8 -*-
"""The active forces window: every forced signal, who, why, and release.

The instructor's audit view over :meth:`VirtualIOBus.active_forces`.
Forces on measurements show the true physics value running underneath.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import theme


class ForceDialog(QDialog):
    COLUMNS = ["Tag", "Forced to", "True value", "By", "Reason", "Since"]

    def __init__(self, bus, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.setWindowTitle("Active forces")
        self.resize(720, 320)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch)
        self.table.setFont(theme.font(9))

        self.info = QLabel("")
        btn_release = QPushButton("Release selected")
        btn_release.clicked.connect(self._release_selected)
        btn_all = QPushButton("Release all")
        btn_all.clicked.connect(self._release_all)

        bar = QHBoxLayout()
        bar.addWidget(self.info, 1)
        bar.addWidget(btn_release)
        bar.addWidget(btn_all)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(700)
        self._refresh()

    def _refresh(self) -> None:
        forces = self.bus.active_forces()
        self.table.setRowCount(len(forces))
        now = time.time()
        for row, rec in enumerate(forces):
            age = int(now - rec.t)
            since = (f"{age // 3600}h {age % 3600 // 60}m" if age >= 3600
                     else f"{age // 60}m {age % 60}s")
            for col, text in enumerate([
                    rec.tag, str(rec.value), str(rec.true_value),
                    rec.source, rec.reason, since]):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, rec.tag)
                self.table.setItem(row, col, item)
        self.info.setText(f"{len(forces)} active force(s)")

    def _release_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None:
                self.bus.release(item.data(Qt.UserRole))
        self._refresh()

    def _release_all(self) -> None:
        self.bus.release_all()
        self._refresh()
