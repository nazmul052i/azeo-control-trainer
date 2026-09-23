"""Dockable panels.

Trends are drawn directly with QPainter rather than pulled in from a plotting
library: six pens at five hertz is not enough work to justify the dependency, and
a hand-drawn plot can show the true process value alongside the measured one,
which is what a transmitter drift exercise needs.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSlider, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.tags import Quality, TagDatabase, TagKind
from ..logging_setup import ring_handler
from . import theme

log = logging.getLogger(__name__)

PEN_COLOURS = ["#1F4E79", "#9E2A2B", "#2F6B3A", "#7A3FA8", "#B5651D", "#0F6E6E"]


# ------------------------------------------------------------------- trends
class TrendCanvas(QWidget):
    """Multi-pen strip chart with autoscaling per pen and a shared time axis."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(220)
        self.pens: List[str] = []
        self.data: Dict[str, Deque[Tuple[float, float]]] = {}
        self.window_s = 600.0
        self.db: Optional[TagDatabase] = None

    def set_pens(self, tags: List[str]) -> None:
        self.pens = tags[:6]
        for t in self.pens:
            self.data.setdefault(t, deque(maxlen=6000))
        self.update()

    def sample(self, snap: Dict) -> None:
        now = time.time()
        for tag in self.pens:
            entry = snap.get(tag)
            if entry is None:
                continue
            value = float(entry[0]) if not isinstance(entry[0], bool) else \
                (1.0 if entry[0] else 0.0)
            buf = self.data.setdefault(tag, deque(maxlen=6000))
            buf.append((now, value))
            while buf and now - buf[0][0] > self.window_s * 1.2:
                buf.popleft()
        self.update()

    def clear(self) -> None:
        for buf in self.data.values():
            buf.clear()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(52, 12, -12, -26)
        p.fillRect(self.rect(), QBrush(theme.PANEL))
        p.fillRect(r, QBrush(QColor("#F6F6F6")))
        p.setPen(QPen(QColor("#C0C0C0"), 1))
        for i in range(1, 5):
            y = r.top() + r.height() * i / 5
            p.drawLine(r.left(), int(y), r.right(), int(y))
        for i in range(1, 6):
            x = r.left() + r.width() * i / 6
            p.drawLine(int(x), r.top(), int(x), r.bottom())
        p.setPen(QPen(QColor("#909090"), 1))
        p.drawRect(r)

        if not self.pens:
            p.setPen(QPen(theme.MUTED_TEXT))
            p.setFont(theme.font(9))
            p.drawText(r, Qt.AlignCenter, "Select tags to trend")
            return

        now = time.time()
        t0 = now - self.window_s
        p.setFont(theme.font(7))
        for idx, tag in enumerate(self.pens):
            buf = [(t, v) for t, v in self.data.get(tag, ()) if t >= t0]
            colour = QColor(PEN_COLOURS[idx % len(PEN_COLOURS)])
            if len(buf) < 2:
                continue
            lo = min(v for _, v in buf)
            hi = max(v for _, v in buf)
            if self.db is not None and tag in self.db:
                tg = self.db[tag]
                if tg.kind.analogue:
                    lo, hi = min(lo, tg.lo), max(hi, tg.hi)
            if hi - lo < 1e-6:
                lo, hi = lo - 1.0, hi + 1.0
            pad = (hi - lo) * 0.06
            lo, hi = lo - pad, hi + pad

            p.setPen(QPen(colour, 1.6))
            prev = None
            for t, v in buf:
                x = r.left() + (t - t0) / self.window_s * r.width()
                y = r.bottom() - (v - lo) / (hi - lo) * r.height()
                if prev is not None:
                    p.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)

            p.setPen(QPen(colour))
            p.drawText(QRectF(r.left() + 6 + idx * 118, r.top() + 4, 116, 12),
                       Qt.AlignLeft, f"{tag}  {buf[-1][1]:.2f}")
            p.drawText(QRectF(2, r.top() + idx * 12, 48, 12),
                       Qt.AlignRight, f"{hi:.0f}")

        p.setPen(QPen(theme.MUTED_TEXT))
        p.setFont(theme.font(7))
        p.drawText(QRectF(r.left(), r.bottom() + 3, 120, 14), Qt.AlignLeft,
                   f"-{self.window_s / 60:.0f} min")
        p.drawText(QRectF(r.right() - 60, r.bottom() + 3, 60, 14), Qt.AlignRight, "now")


class TrendPanel(QWidget):
    def __init__(self, db: TagDatabase) -> None:
        super().__init__()
        self.db = db
        self.canvas = TrendCanvas()
        self.canvas.db = db

        self.selector = QListWidget()
        self.selector.setSelectionMode(QAbstractItemView.MultiSelection)
        self.selector.setMaximumWidth(190)
        for tag in db.all():
            item = QListWidgetItem(tag.name)
            item.setToolTip(tag.desc)
            self.selector.addItem(item)
        self.selector.itemSelectionChanged.connect(self._selection_changed)

        self.window_box = QComboBox()
        for label, seconds in [("2 min", 120), ("10 min", 600), ("30 min", 1800),
                               ("1 hour", 3600), ("4 hours", 14400)]:
            self.window_box.addItem(label, seconds)
        self.window_box.setCurrentIndex(1)
        self.window_box.currentIndexChanged.connect(
            lambda: setattr(self.canvas, "window_s",
                            float(self.window_box.currentData())))

        clear = QPushButton("Clear history")
        clear.clicked.connect(self.canvas.clear)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Window"))
        controls.addWidget(self.window_box)
        controls.addStretch(1)
        controls.addWidget(clear)

        right = QVBoxLayout()
        right.addLayout(controls)
        right.addWidget(self.canvas, 1)

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel("Pens (up to six)"))
        left.addWidget(self.selector, 1)
        layout.addLayout(left)
        layout.addLayout(right, 1)

    def _selection_changed(self) -> None:
        tags = [i.text() for i in self.selector.selectedItems()][:6]
        self.canvas.set_pens(tags)

    def refresh(self, snap: Dict) -> None:
        self.canvas.sample(snap)


# --------------------------------------------------------------- tag browser
class TagPanel(QWidget):
    """Filterable table of every tag with live value, quality and NodeId."""

    tag_activated = Signal(str)

    COLUMNS = ["Tag", "Type", "Unit", "Description", "Value", "EU", "Quality",
               "OPC UA NodeId"]

    def __init__(self, db: TagDatabase) -> None:
        super().__init__()
        self.db = db
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by tag, unit or description")
        self.filter.textChanged.connect(self._rebuild)

        self.kind_box = QComboBox()
        self.kind_box.addItem("All types", "")
        for k in TagKind:
            self.kind_box.addItem(k.value, k.value)
        self.kind_box.currentIndexChanged.connect(self._rebuild)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(
            lambda row, _c: self.tag_activated.emit(self.table.item(row, 0).text()))

        top = QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.kind_box)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)

        self._rows: List[str] = []
        self._rebuild()

    def _rebuild(self) -> None:
        needle = self.filter.text().strip().lower()
        kind = self.kind_box.currentData()
        rows = [t for t in self.db.all()
                if (not kind or t.kind.value == kind)
                and (not needle or needle in t.name.lower()
                     or needle in t.desc.lower() or needle in t.unit.lower())]
        self.table.setRowCount(len(rows))
        self._rows = [t.name for t in rows]
        for r, tag in enumerate(rows):
            for c, text in enumerate([tag.name, tag.kind.value, tag.unit, tag.desc,
                                      "", tag.eu, "", f"ns=2;s={tag.name}"]):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setFont(theme.font(9, True, mono=True))
                self.table.setItem(r, c, item)
        for c in (0, 1, 2, 4, 5, 6):
            self.table.resizeColumnToContents(c)

    def refresh(self, snap: Dict) -> None:
        for r, name in enumerate(self._rows):
            entry = snap.get(name)
            if entry is None:
                continue
            tag = self.db.get(name)
            value = entry[0]
            text = (tag.state1 if value else tag.state0) if isinstance(value, bool) \
                else f"{float(value):.3f}"
            cell = self.table.item(r, 4)
            if cell:
                cell.setText(text)
            q = Quality(int(entry[1]))
            qcell = self.table.item(r, 6)
            if qcell:
                qcell.setText(q.label)
                qcell.setForeground(QBrush(theme.quality_colour(int(q))))


# -------------------------------------------------------------- malfunctions
class MalfunctionPanel(QWidget):
    """Instructor fault injection, one row per malfunction."""

    def __init__(self, flowsheet) -> None:
        super().__init__()
        self.flowsheet = flowsheet
        self.rows: List[Tuple[object, QCheckBox, QDoubleSpinBox]] = []

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(
            ["Active", "ID", "Target", "Description", "Category", "Parameter"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        mfs = flowsheet.malfunctions()
        table.setRowCount(len(mfs))
        for r, mf in enumerate(mfs):
            box = QCheckBox()
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(8, 0, 0, 0)
            hl.addWidget(box)
            table.setCellWidget(r, 0, holder)

            for c, text in enumerate([mf.mf_id, mf.target, mf.description, mf.category], 1):
                table.setItem(r, c, QTableWidgetItem(text))

            spin = QDoubleSpinBox()
            if mf.param_label:
                spin.setRange(mf.param_min, mf.param_max)
                spin.setValue(mf.param_min)
                spin.setSuffix(f"  {mf.param_label}")
                spin.setDecimals(2)
            else:
                spin.setEnabled(False)
                spin.setSpecialValueText("n/a")
            table.setCellWidget(r, 5, spin)

            box.toggled.connect(
                lambda checked, m=mf, s=spin: m.set(checked, s.value()))
            spin.valueChanged.connect(
                lambda value, m=mf, b=box: m.set(b.isChecked(), value))
            self.rows.append((mf, box, spin))

        for c in (0, 1, 2, 4):
            table.resizeColumnToContents(c)
        # The parameter spin boxes carry their label as a suffix ("Stick band",
        # "Travel factor"); the default column width clips it.
        param_w = max((s.sizeHint().width() for _m, _b, s in self.rows),
                      default=120)
        table.setColumnWidth(5, param_w + 24)

        clear = QPushButton("Clear all malfunctions")
        clear.clicked.connect(self._clear_all)
        self.summary = QLabel("No active malfunctions")

        bar = QHBoxLayout()
        bar.addWidget(self.summary, 1)
        bar.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.addWidget(table, 1)
        layout.addLayout(bar)
        self.table = table

    def _clear_all(self) -> None:
        for _mf, box, _spin in self.rows:
            box.setChecked(False)
        self.flowsheet.clear_all_malfunctions()

    def refresh(self, _snap: Dict) -> None:
        active = [mf.mf_id for mf, box, _ in self.rows if box.isChecked()]
        self.summary.setText(
            f"{len(active)} active: {', '.join(active)}" if active
            else "No active malfunctions")
        self.summary.setStyleSheet(
            f"color: {theme.ALARM_HIGH.name()};" if active else "")


# ------------------------------------------------------------- field operations
class FieldOpsPanel(QWidget):
    """Field manual valves and other things only a field operator can touch.

    These deliberately have no DCS command tags. A trainee on the operator station can
    see the limit switches and nothing else, which is exactly the situation that
    makes a two-person exercise worth running.
    """

    def __init__(self, flowsheet) -> None:
        super().__init__()
        self.flowsheet = flowsheet
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Field manual valves. The DCS sees only the limit switches."))

        u100 = flowsheet.unit("U100")
        u300 = flowsheet.unit("U300")

        box = QGroupBox("HV-1001  D1 drain manual valve")
        form = QFormLayout(box)
        self.hv1001 = QSlider(Qt.Horizontal)
        self.hv1001.setRange(0, 100)
        self.hv1001.setValue(int(getattr(u100, "hv1001_position", 0.0)))
        self.hv1001_label = QLabel("0 % open")
        self.hv1001.valueChanged.connect(self._set_hv1001)
        form.addRow("Position", self.hv1001)
        form.addRow("", self.hv1001_label)
        layout.addWidget(box)

        box2 = QGroupBox("HV-3001  H1 fuel oil manual isolation")
        form2 = QFormLayout(box2)
        self.hv3001 = QCheckBox("Open")
        self.hv3001.setChecked(bool(getattr(u300, "fuel_oil_available", False)))
        self.hv3001.toggled.connect(
            lambda v: setattr(self.flowsheet.unit("U300"), "fuel_oil_available", v))
        form2.addRow("Position", self.hv3001)
        layout.addWidget(box2)

        box3 = QGroupBox("Local resets")
        form3 = QFormLayout(box3)
        reset_mov_a = QPushButton("Reset MOV-1001A torque switch")
        reset_mov_a.clicked.connect(lambda: self.flowsheet.unit("U100").mov_a.reset())
        reset_mov_b = QPushButton("Reset MOV-1001B torque switch")
        reset_mov_b.clicked.connect(lambda: self.flowsheet.unit("U100").mov_b.reset())
        reset_pa = QPushButton("Reset P-101A motor trip")
        reset_pa.clicked.connect(lambda: self.flowsheet.unit("U100").motor_a.reset())
        for w in (reset_mov_a, reset_mov_b, reset_pa):
            form3.addRow(w)
        layout.addWidget(box3)

        box4 = QGroupBox("Local / remote selectors")
        form4 = QFormLayout(box4)
        self.mov_a_remote = QCheckBox("MOV-1001A in remote")
        self.mov_a_remote.setChecked(True)
        self.mov_a_remote.toggled.connect(self._set_remote_a)
        form4.addRow(self.mov_a_remote)
        layout.addWidget(box4)
        layout.addStretch(1)

    def _set_hv1001(self, value: int) -> None:
        unit = self.flowsheet.unit("U100")
        if unit is not None:
            unit.hv1001_position = float(value)
        self.hv1001_label.setText(f"{value} % open")

    def _set_remote_a(self, remote: bool) -> None:
        db_tag = self.flowsheet.db.get("XS-MOV1001A-AVL")
        if db_tag is not None:
            with self.flowsheet.db.lock:
                db_tag.value = bool(remote)

    def refresh(self, _snap: Dict) -> None:
        return None


# ---------------------------------------------------------------------- comms
class CommsPanel(QWidget):
    """OPC UA session state. Half of commissioning is finding the dead tag."""

    def __init__(self, server, db: TagDatabase) -> None:
        super().__init__()
        self.server = server
        self.db = db
        self.fields: Dict[str, QLabel] = {}

        box = QGroupBox("OPC UA server")
        form = QFormLayout(box)
        for key in ["Status", "Endpoint", "Namespace", "Publish period",
                    "Tag nodes", "Writable nodes",
                    "Client writes received", "Values published", "Publish failures",
                    "Last error"]:
            label = QLabel("-")
            label.setFont(theme.font(9, mono=True))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.fields[key] = label
            form.addRow(key, label)

        hint = QLabel(
            "Node identifiers are the tag name at namespace 2, for example "
            "ns=2;s=LT-1001. Companion nodes ns=2;s=LT-1001.PctVal and "
            "ns=2;s=LT-1001.Status carry percent of span and quality.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.MUTED_TEXT.name()};")

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(hint)
        layout.addStretch(1)

    def refresh(self, _snap: Dict) -> None:
        st = self.server.stats
        counts = self.db.counts()
        writable = counts["AO"] + counts["DO"]
        values = {
            "Status": "RUNNING" if st.running else
                      ("STARTING" if not st.started else "STOPPED"),
            "Endpoint": st.endpoint,
            "Namespace": "2  (urn:azeoplant:simulator)",
            "Publish period": (f"{self.server.publish_period:.2f} s"
                               if hasattr(self.server, "publish_period") else "-"),
            "Tag nodes": str(st.node_count),
            "Writable nodes": str(writable),
            "Client writes received": str(st.writes_in),
            "Values published": str(st.writes_out),
            "Publish failures": str(getattr(st, "publish_errors", 0)),
            "Last error": st.last_error or "none",
        }
        for key, text in values.items():
            self.fields[key].setText(text)
        colour = theme.RUNNING if st.running else theme.ALARM_CRITICAL
        self.fields["Status"].setStyleSheet(f"color: {colour.name()}; font-weight: bold;")
        if getattr(st, "publish_errors", 0):
            self.fields["Publish failures"].setStyleSheet(
                f"color: {theme.ALARM_CRITICAL.name()}; font-weight: bold;")
        if st.last_error:
            self.fields["Last error"].setStyleSheet(
                f"color: {theme.ALARM_CRITICAL.name()};")


# -------------------------------------------------------------- engine health
class EnginePanel(QWidget):
    """The numbers behind the status bar summary.

    Shows what the one-line summary cannot: peak scan time against the step
    budget, the error budget state, and where the milliseconds go unit by
    unit - the first place to look when the engine starts overrunning.
    """

    def __init__(self, engine, flowsheet) -> None:
        super().__init__()
        self.engine = engine
        self.flowsheet = flowsheet
        self.fields: Dict[str, QLabel] = {}

        box = QGroupBox("Engine")
        form = QFormLayout(box)
        for key in ["State", "Integration step", "Speed factor", "Sim time",
                    "Heartbeat", "Scan time", "Scan peak", "Overruns",
                    "Error budget", "Model errors", "Out of range",
                    "Last error"]:
            label = QLabel("-")
            label.setFont(theme.font(9, mono=True))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.fields[key] = label
            form.addRow(key, label)

        reset_peak = QPushButton("Reset peak")
        reset_peak.clicked.connect(self._reset_peak)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(reset_peak)

        self.unit_table = QTableWidget(0, 3)
        self.unit_table.setHorizontalHeaderLabels(["Unit", "Name", "Step ms"])
        self.unit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.unit_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.unit_table.verticalHeader().setVisible(False)
        self.unit_table.setFont(theme.font(8, mono=True))
        header = self.unit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        units_box = QGroupBox("Per-unit step time, slowest first")
        units_layout = QVBoxLayout(units_box)
        units_layout.addWidget(self.unit_table)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addLayout(bar)
        layout.addWidget(units_box, 1)

    def _reset_peak(self) -> None:
        self.engine.stats.scan_time_peak_ms = 0.0

    def _excursion_text(self) -> str:
        # Analogues the models have driven past what an instrument can show.
        # A non-empty answer is a model defect, not a process upset, which is
        # why it belongs on the panel an instructor checks when a trend does
        # not smell right.
        ex = self.engine.db.excursions()
        if not ex:
            return "none"
        worst = ", ".join(f"{n} x{c}" for n, c, _w in ex[:3])
        more = f" +{len(ex) - 3} more" if len(ex) > 3 else ""
        return f"{len(ex)} tags: {worst}{more}"

    def refresh(self, _snap: Dict) -> None:
        st = self.engine.stats
        running = st.running and not st.frozen_by_error
        state = "FROZEN (ERROR)" if st.frozen_by_error else (
            "RUNNING" if running else "FROZEN")
        budget_ms = self.engine.dt / max(st.speed_factor, 1e-9) * 1000.0
        consecutive = getattr(self.engine, "_consecutive_errors", 0)
        hours, rem = divmod(int(st.sim_time), 3600)
        values = {
            "State": state,
            "Integration step": f"{self.engine.dt * 1000.0:.0f} ms  (fixed at startup)",
            "Speed factor": f"{st.speed_factor:g}x",
            "Sim time": f"{hours:02d}:{rem // 60:02d}:{rem % 60:02d}",
            "Heartbeat": str(st.heartbeat),
            "Scan time": f"{st.scan_time_ms:6.1f} ms of {budget_ms:.0f} ms budget",
            "Scan peak": f"{st.scan_time_peak_ms:6.1f} ms",
            "Overruns": str(st.overruns),
            "Error budget": f"{consecutive} of {self.engine.ERROR_BUDGET} "
                            "failed steps",
            "Model errors": str(st.errors),
            "Out of range": self._excursion_text(),
            "Last error": st.last_error or "none",
        }
        for key, text in values.items():
            self.fields[key].setText(text)

        colour = theme.ALARM_CRITICAL if st.frozen_by_error else (
            theme.RUNNING if running else theme.ALARM_HIGH)
        self.fields["State"].setStyleSheet(
            f"color: {colour.name()}; font-weight: bold;")
        self.fields["Scan time"].setStyleSheet(
            f"color: {theme.ALARM_HIGH.name()};"
            if st.scan_time_ms > budget_ms else "")
        self.fields["Error budget"].setStyleSheet(
            f"color: {theme.ALARM_CRITICAL.name()}; font-weight: bold;"
            if consecutive else "")
        self.fields["Last error"].setStyleSheet(
            f"color: {theme.ALARM_CRITICAL.name()};" if st.last_error else "")
        self.fields["Out of range"].setStyleSheet(
            f"color: {theme.ALARM_HIGH.name()}; font-weight: bold;"
            if self.fields["Out of range"].text() != "none" else "")

        rows = sorted(st.unit_ms.items(), key=lambda kv: kv[1], reverse=True)
        self.unit_table.setRowCount(len(rows))
        for row, (code, ms) in enumerate(rows):
            unit = self.flowsheet.unit(code)
            name = unit.name if unit is not None else ""
            for col, text in enumerate([code, name, f"{ms:7.2f}"]):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.unit_table.setItem(row, col, item)


# ------------------------------------------------------------------- log view
class LogPanel(QWidget):
    """Tail of the in-memory log ring buffer."""

    def __init__(self) -> None:
        super().__init__()
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(4000)
        self.view.setFont(theme.font(8, mono=True))

        self.level_box = QComboBox()
        for name, value in [("Debug", 10), ("Info", 20), ("Warning", 30),
                            ("Error", 40)]:
            self.level_box.addItem(name, value)
        self.level_box.setCurrentIndex(1)

        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Minimum level"))
        bar.addWidget(self.level_box)
        bar.addStretch(1)
        bar.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)
        self._marker = 0

    def _clear(self) -> None:
        ring_handler.clear()
        self.view.clear()
        self._marker = 0

    def refresh(self, _snap: Dict) -> None:
        threshold = int(self.level_box.currentData())
        self._marker, records = ring_handler.since(self._marker)
        for level, text in records:
            if level < threshold:
                continue
            self.view.appendPlainText(text)

class LoopsPanel(QWidget):
    """Every control module at a glance; double click opens its faceplate."""

    def __init__(self, controller, open_faceplate) -> None:
        super().__init__()
        self.controller = controller
        self._open = open_faceplate
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Module", "Description", "Mode", "PV", "SP", "OUT %"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setFont(theme.font(9))
        self.table.cellDoubleClicked.connect(self._dbl)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        self._rows: list = []

    def _dbl(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._rows):
            self._open(self._rows[row])

    def refresh(self, _snap) -> None:
        if self.controller is None:
            return
        loops = [l for m, l in sorted(self.controller.loops.items())
                 if m not in getattr(self.controller, "_ratio", {})]
        self._rows = [l.module for l in loops]
        self.table.setRowCount(len(loops))
        for r, loop in enumerate(loops):
            pid = loop.pid
            shed = pid.actual_mode != pid.target_mode
            cells = [loop.module, loop.description, pid.actual_mode.value,
                     f"{pid.pv:.2f}", f"{pid.sp_wrk:.2f}", f"{pid.out:.1f}"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2 and shed:
                    item.setForeground(theme.ALARM_HIGH)
                if c == 0 and pid.alarms.any_active():
                    item.setForeground(theme.ALARM_CRITICAL)
                self.table.setItem(r, c, item)
