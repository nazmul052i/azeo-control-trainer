"""Archive selection, evidence export and measured response comparison UI."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from html import escape
import base64
import csv
import json
import math
from pathlib import Path

import numpy as np
from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QBuffer, QDateTime, QIODevice, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QDateTimeEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.headless import is_headless
from .analysis import aligned_response, history_response_rows, response_metrics, session_response_rows
from .export import export_history, memory_rows
from .historian import PEN_COLOURS
from ..theme.widgets import bind_operator_theme


def chart_png(chart, interval=None):
    """Capture Qt on its owning thread; the export worker only receives bytes."""
    from pyqtgraph.exporters import ImageExporter
    before = chart._plot.vb.viewRange()[0]
    try:
        if interval:
            chart._plot.setXRange(*interval, padding=0)
        exporter = ImageExporter(chart._plot)
        exporter.parameters()["width"] = 1600
        image = exporter.export(toBytes=True)
        pens = [pen for pen in chart._pens.values() if pen.visible]
        legend_height = 24 + math.ceil(len(pens) / 3) * 30
        combined = QImage(image.width(), image.height() + legend_height, QImage.Format_ARGB32)
        combined.fill(Qt.white)
        painter = QPainter(combined)
        painter.drawImage(0, 0, image)
        painter.setFont(QFont("Segoe UI", 10))
        column_width = image.width() // 3
        for index, pen in enumerate(pens):
            x, y = 14 + index % 3 * column_width, image.height() + 25 + index // 3 * 30
            painter.setPen(QPen(QColor(pen.color), 3, Qt.DashLine if pen.line_style == "dash" else Qt.SolidLine))
            painter.drawLine(x, y - 5, x + 25, y - 5)
            painter.setPen(QColor("#2B323B"))
            label = painter.fontMetrics().elidedText(f"{pen.label} [{pen.unit}]", Qt.ElideRight, column_width - 55)
            painter.drawText(x + 34, y, label)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        combined.save(buffer, "PNG")
        return bytes(buffer.data())
    finally:
        if interval:
            chart._plot.setXRange(*before, padding=0)


def snapshot_png(snapshot, paths, colours, interval):
    from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget, TrendPen
    chart = HistorianTrendWidget("Archived process history")
    chart.setAttribute(Qt.WA_DontShowOnScreen)
    chart.set_embedded_controls()
    chart.set_recorded_mode()
    chart.resize(1600, 800)
    units = {snapshot.TAGS[path].unit for path in paths}
    for index, path in enumerate(paths):
        point = snapshot.TAGS[path]
        chart.add_pen(TrendPen(path, point.label or path, point.unit,
                               colours.get(path, PEN_COLOURS[index % len(PEN_COLOURS)]), point.lo, point.hi,
                               axis="right" if point.unit == "%" and len(units) > 1 else "left",
                               line_style="dash" if path.endswith("/SP") else "solid"))
    if len(units) > 2:
        chart.set_normalized(True)
    try:
        chart.update_all(snapshot)
        chart.set_review_range(*interval)
        chart.show()
        QApplication.processEvents()
        return chart_png(chart, interval)
    finally:
        chart.close()
        chart.deleteLater()


def poll_export_jobs(view):
    for job in list(view._export_jobs):
        if not job["future"].done():
            continue
        try:
            if job["phase"] == "render":
                snapshot = job["future"].result()
                # Remove before rendering: processing Qt's layout events may
                # reenter the view's timer, which must not render this job twice.
                view._export_jobs.remove(job)
                job["arguments"]["png"] = snapshot_png(snapshot, job["arguments"]["paths"],
                                                        job["colours"], job["interval"])
                if view._closed:
                    job["completion"].cancel()
                    continue
                if job["target"].suffix == ".png":
                    job["target"].write_bytes(job["arguments"]["png"])
                    job["completion"].set_result(job["target"])
                    continue
                job["future"] = view.historian.archive._submit(
                    lambda entry=job: export_history(entry["target"], **entry["arguments"]))
                job["phase"] = "write"
                view._export_jobs.append(job)
            else:
                job["completion"].set_result(job["future"].result())
                view._export_jobs.remove(job)
        except Exception as error:  # noqa: BLE001
            if job in view._export_jobs:
                view._export_jobs.remove(job)
            if not job["completion"].done():
                job["completion"].set_exception(error)


class ArchiveRangeDialog(QDialog):
    def __init__(self, historian, parent=None):
        super().__init__(parent)
        self.historian = historian
        self.setWindowTitle("Historical interval and retention")
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self.resize(520, 280)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Choose a local date and time. Collection continues while you review history."))
        form = QFormLayout()
        self.start = QDateTimeEdit()
        self.end = QDateTimeEdit()
        now = historian.origin + historian.now()
        for editor, value in ((self.start, now - 3600), (self.end, now)):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy  HH:mm:ss")
            editor.setDateTime(QDateTime.fromMSecsSinceEpoch(int(value * 1000)))
        form.addRow("From", self.start)
        form.addRow("Through", self.end)
        self.retention = QSpinBox()
        self.retention.setRange(1, 365)
        self.retention.setSuffix(" days")
        self.retention.setValue(historian.archive.retention_days if historian.archive else 1)
        self.retention.setEnabled(historian.archive is not None)
        form.addRow("Disk retention", self.retention)
        self.budget = QSpinBox()
        self.budget.setRange(64, 16384)
        self.budget.setSuffix(" MB")
        self.budget.setValue(historian.archive.storage_limit_mb if historian.archive else 1024)
        self.budget.setEnabled(historian.archive is not None)
        form.addRow("Storage budget", self.budget)
        root.addLayout(form)
        note = QLabel("Older samples expire at the time or storage limit. Cleanup runs periodically; file size may temporarily exceed the budget. "
                      "Reducing either limit removes older archived data at the next cleanup."
                      if historian.archive else "This view has memory history only; disk retention is unavailable.")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        bind_operator_theme(self, tool_controls=True)

    def interval(self):
        return tuple((editor.dateTime().toMSecsSinceEpoch() / 1000 - self.historian.origin) / 60
                     for editor in (self.start, self.end))


class ExportHistoryDialog(QDialog):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setWindowTitle("Export history evidence")
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self.resize(560, 340)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.scope = AuthoringComboBox()
        self.scope.addItems(("Visible interval", "Between A and B", "All retained history"))
        self.format = AuthoringComboBox()
        self.format.addItems(("HTML report + raw CSV + events", "Raw CSV + events + metadata", "Chart PNG"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Purpose, observations and instructor notes")
        self.notes.setMaximumHeight(100)
        form.addRow("Scope", self.scope)
        form.addRow("Format", self.format)
        form.addRow("Notes", self.notes)
        root.addLayout(form)
        self.path = QLineEdit()
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        browse = QPushButton("Choose file…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        root.addLayout(row)
        info = QLabel("Raw exports include UTC timestamps, simulation time, quality, units and run identity. "
                      "Event times describe observations, not controller scan ordering.")
        info.setWordWrap(True)
        root.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        bind_operator_theme(self, tool_controls=True)

    def browse(self):
        if is_headless():
            return
        suffix = ("html", "csv", "png")[self.format.currentIndex()]
        target, _ = QFileDialog.getSaveFileName(self, "Export history", f"history.{suffix}", f"{suffix.upper()} (*.{suffix})")
        if target:
            self.path.setText(target)

    def _accept(self):
        if not self.path.text().strip():
            self.browse()
        if self.path.text().strip():
            self.accept()

    def export(self):
        view = self.view
        suffix = (".html", ".csv", ".png")[self.format.currentIndex()]
        target = Path(self.path.text()).with_suffix(suffix).resolve()
        start, end = view._chart.visible_time_range()
        if self.scope.currentIndex() == 1:
            if not view._chart._ab_button.isChecked():
                raise ValueError("Enable and position A/B cursors before exporting that interval")
            start, end = sorted((view._chart._a_line.value(), view._chart._b_line.value()))
        elif self.scope.currentIndex() == 2:
            start, end = 0, view.historian.now() / 60
            if view.historian.archive and view.historian.archive.available_start is not None:
                start = (view.historian.archive.available_start - view.historian.origin) / 60
        start = 0 if start is None else start
        end = view.historian.now() / 60 if end is None else end
        if end <= start or not view._pens:
            raise ValueError("Choose a non-empty interval and at least one pen")
        needs_archive_image = self.scope.currentIndex() == 2 and view.historian.archive is not None and suffix != ".csv"
        png = chart_png(view._chart, (start, end)) if suffix != ".csv" and not needs_archive_image else b""
        if suffix == ".png" and not needs_archive_image:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png)
            return None, target
        historian = view.historian
        metadata = {path: historian.point_metadata(historian.TAGS[path]) for path in view._pens}
        arguments = dict(paths=list(view._pens), metadata=metadata, origin=historian.origin,
                         start=start * 60, end=end * 60, archive=historian.archive,
                         events=list(view._source.events), png=png, notes=self.notes.toPlainText())
        if needs_archive_image:
            target.parent.mkdir(parents=True, exist_ok=True)
            future = historian.query_async(view._pens, start, end, as_snapshot=True)
            completion = Future()
            view._export_jobs.append({"future": future, "phase": "render", "completion": completion,
                                      "target": target, "arguments": arguments,
                                      "colours": dict(view._colours), "interval": (start, end)})
            return completion, target
        if historian.archive:
            future = historian.archive._submit(lambda: export_history(target, **arguments))
        else:
            arguments["rows"] = memory_rows(view._source, view._pens, start * 60, end * 60)
            # No retained disk history means a bounded memory buffer. The worker
            # owns immutable copies and may finish after the view is closed.
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-export")
            future = pool.submit(export_history, target, **arguments)
            pool.shutdown(wait=False)
        return future, target


class RunComparisonDialog(QDialog):
    """Compare saved attempts or captured review intervals using the existing metrics."""

    def __init__(self, historian, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.historian = historian
        self.owner_view = parent if hasattr(parent, "_source") else None
        self.archive = getattr(historian, "training_archive", None)
        self._runs = {}
        self._measured = {}
        self._jobs = []
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-comparison")
        self.setWindowTitle("Compare process responses")
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.resize(1260, 850)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        root = QVBoxLayout(self)
        self.status = QLabel("Capture a baseline and trial from the current interval, or load two saved training attempts.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        choices = QHBoxLayout()
        self.loop = AuthoringComboBox()
        bases = sorted({path.rsplit("/", 1)[0] for path in historian.TAGS
                        if path.endswith("/PV") and path[:-2] + "SP" in historian.TAGS
                        and path[:-2] + "OUT" in historian.TAGS})
        if not bases:
            bases = sorted({path.rsplit("/", 1)[0] for path in historian.TAGS if path.endswith("/PV")})
        self.loop.addItems(bases)
        self.loop.currentTextChanged.connect(self._change_loop)
        if self.owner_view and self.owner_view._pens:
            candidates = [path.rsplit("/", 1)[0] for path in self.owner_view._pens]
            self.loop.setCurrentText(next((base for base in candidates if base in bases), bases[0] if bases else ""))
        choices.addWidget(QLabel("Loop"))
        choices.addWidget(self.loop, 1)
        self.alignment = AuthoringComboBox()
        self.alignment.addItems(("Start of selection", "First setpoint change", "Fault introduction"))
        self.alignment.currentIndexChanged.connect(self.refresh)
        choices.addWidget(self.alignment)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(.000001, 1e9)
        self.tolerance.setDecimals(3)
        self.tolerance.setValue(1)
        self.tolerance.setSuffix(" EU tolerance")
        self.tolerance.valueChanged.connect(self.refresh)
        choices.addWidget(self.tolerance)
        root.addLayout(choices)
        self.sessions = AuthoringComboBox()
        self.sessions.addItem("Current chart interval (A/B if enabled)", None)
        if self.archive:
            for row in self.archive.sessions():
                stamp = datetime.fromtimestamp(row["started"]).strftime("%d %b %H:%M")
                self.sessions.addItem(f"{stamp} · {row['title']}", row["id"])
        row = QHBoxLayout()
        row.addWidget(self.sessions, 1)
        for label, name in (("Use as baseline", "baseline"), ("Use as trial", "trial")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, which=name: self.load(which))
            row.addWidget(button)
        export = QPushButton("Export comparison…")
        export.clicked.connect(self.export)
        row.addWidget(export)
        root.addLayout(row)
        from azeo_control_trainer.core.pid.charts.historian_trend import HistorianTrendWidget
        self.chart = HistorianTrendWidget("Response comparison · aligned elapsed time")
        self.chart.set_recorded_mode()
        self.chart.context_services = {"export": lambda _: self.export()}
        self.chart._plot.setLabel("bottom", "Time from alignment", units="min")
        self.chart._png_button.hide()
        self.chart._csv_button.hide()
        root.addWidget(self.chart, 1)
        self.metrics = QTableWidget(0, 3)
        self.metrics.setHorizontalHeaderLabels(("Measurement", "Baseline", "Trial"))
        self.metrics.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.metrics.verticalHeader().hide()
        self.metrics.setEditTriggers(QTableWidget.NoEditTriggers)
        self.metrics.setMaximumHeight(210)
        self.metrics.setMinimumHeight(190)
        self.metrics.verticalHeader().setDefaultSectionSize(26)
        root.addWidget(self.metrics)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Instructor observations and comparison conditions")
        self.notes.setMaximumHeight(75)
        root.addWidget(self.notes)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        bind_operator_theme(self, tool_controls=True)

    def apply_operator_theme(self, theme):
        self.chart.apply_operator_theme(theme)

    def load(self, name):
        identity = self.sessions.currentData()
        if identity and self.archive:
            self._jobs.append((self._pool.submit(self.archive.read, identity), name))
            self.status.setText(f"Loading {name}…")
            return
        try:
            if self.owner_view is None:
                raise ValueError("Open Process History to select an interval, or choose a saved attempt")
            view = self.owner_view
            if view._source.reduced:
                raise ValueError("Select a shorter interval; response metrics require raw samples")
            start, end = view._chart.visible_time_range()
            if view._chart._ab_button.isChecked():
                start, end = sorted((view._chart._a_line.value(), view._chart._b_line.value()))
            rows = history_response_rows(view._source, self.loop.currentText(),
                                         (start or 0) * 60, (end or view.historian.now() / 60) * 60)
            self._runs[name] = {"rows": rows, "events": list(view._source.events), "title": "Selected historian interval",
                                "loop": self.loop.currentText(), "unit": self._unit(self.loop.currentText())}
            self.refresh()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))

    def _unit(self, loop):
        point = self.historian.TAGS.get(loop + "/PV")
        return point.unit if point else "EU"

    def _change_loop(self, *_):
        self._runs = {}
        self._measured = {}
        if hasattr(self, "chart"):
            self.chart.remove_all_pens()
            self.metrics.setRowCount(0)
            self.status.setText("Select the baseline and trial for this loop")

    def _poll(self):
        for future, name in list(self._jobs):
            if not future.done():
                continue
            self._jobs.remove((future, name))
            try:
                data = future.result()
                base = data["exercise"]["loop"]
                if base != self.loop.currentText():
                    raise ValueError(f"This attempt is for {base}; select that loop before loading it")
                unit = next((r["values"].get(base + "/PV", {}).get("units", "") for r in data["samples"]), "")
                self._runs[name] = {"rows": session_response_rows(data), "events": data["events"],
                                    "title": data["exercise"]["name"], "loop": base, "unit": unit or self._unit(base)}
                self.refresh()
            except Exception as error:  # noqa: BLE001
                self.status.setText(f"Could not load attempt: {error}")

    def set_windows(self, windows, loop):
        self.loop.setCurrentText(loop)
        self._runs = {name: {"rows": list(rows), "events": [], "title": f"Measured {name}",
                             "loop": loop, "unit": self._unit(loop)} for name, rows in windows.items()}
        self.refresh()

    def refresh(self, *_):
        if not hasattr(self, "chart"):
            return
        from azeo_control_trainer.core.pid.charts.historian_trend import TrendPen
        alignment = ("start", "setpoint", "fault")[self.alignment.currentIndex()]
        self.chart.remove_all_pens()
        self._measured = {}
        notes = []
        units = {run["unit"] for run in self._runs.values()}
        if len(units) > 1:
            self.status.setText("These recordings use different engineering units; select matching responses")
            return
        for group, name in enumerate(("baseline", "trial")):
            if name not in self._runs:
                continue
            run = self._runs[name]
            try:
                aligned = aligned_response(run["rows"], alignment, run["events"])
                result = response_metrics(aligned["measurement"], tolerance=self.tolerance.value())
            except ValueError as error:
                notes.append(f"{name}: {error}")
                continue
            self._measured[name] = {"metrics": result, "aligned": aligned}
            notes.append(f"{name}: {result['note']}")
            rows = aligned["plot"]
            times = np.asarray([row["time"] / 60 for row in rows])
            for index, suffix in enumerate(("pv", "sp", "out")):
                path = f"{name}/{suffix}"
                unit = "%" if suffix == "out" else run["unit"]
                samples = np.asarray([float(row[suffix]) if row.get("quality") == "GOOD" and
                                      isinstance(row.get(suffix), (int, float)) else math.nan for row in rows])
                good = samples[np.isfinite(samples)]
                lo, hi = (float(good.min()), float(good.max())) if good.size else (0, 100)
                pad = max(1, (hi - lo) * .1)
                self.chart.add_pen(TrendPen(path, f"{name.title()} {suffix.upper()}", unit,
                                            PEN_COLOURS[index + group * 3], lo - pad, hi + pad,
                                            axis="right" if suffix == "out" else "left",
                                            line_style="dash" if group else "solid"))
                self.chart.update_data(path, times, samples)
        self.chart.fit_data()
        self.chart._view_right.setRange(yRange=(0, 100), padding=0)
        keys = (("Observed seconds", "observed_seconds"), ("Accumulated error (EU·s)", "iae"),
                ("Overshoot (EU)", "overshoot"), ("Settling time (s)", "settling_seconds"),
                ("Good samples", "samples"), ("Total samples", "total_samples"))
        self.metrics.setRowCount(len(keys))
        for row, (label, key) in enumerate(keys):
            self.metrics.setItem(row, 0, QTableWidgetItem(label))
            for column, name in enumerate(("baseline", "trial"), 1):
                value = self._measured.get(name, {}).get("metrics", {}).get(key)
                self.metrics.setItem(row, column, QTableWidgetItem(f"{value:,.3f}" if isinstance(value, float)
                                                                else str(value) if value is not None else "Unavailable"))
        self.status.setText(" · ".join(notes) or "Choose two response windows")

    def export(self, target=None):
        if not self._measured:
            self.status.setText("Load a response before exporting")
            return
        if not isinstance(target, (str, Path)):
            if is_headless():
                return
            target, _ = QFileDialog.getSaveFileName(self, "Export response comparison", "comparison.html", "HTML (*.html)")
        if not target:
            return
        target = Path(target).with_suffix(".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            png = chart_png(self.chart)
            body = ["<!doctype html><html><meta charset='utf-8'><title>Response comparison</title>",
                    "<style>body{font:15px Segoe UI;margin:40px;color:#2b323b}h1{color:#004487}img{width:100%}"
                    "table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #d8dadc}</style>",
                    f"<h1>Response comparison · {escape(self.loop.currentText())}</h1>",
                    f"<p>Alignment: {escape(self.alignment.currentText())}. Tolerance: {self.tolerance.value():g} EU.</p>",
                    f'<img alt="Aligned baseline and trial" src="data:image/png;base64,{base64.b64encode(png).decode()}">',
                    f"<p>{escape(self.status.text())}</p><p>{escape(self.notes.toPlainText())}</p><table>"]
            for row in range(self.metrics.rowCount()):
                body.append("<tr>" + "".join(f"<td>{escape(self.metrics.item(row, column).text())}</td>" for column in range(3)) + "</tr>")
            body.append("</table><p>Measurements support instructor review; they do not assign a trainee grade.</p></html>")
            target.write_text("\n".join(body), encoding="utf-8")
            clean = json.loads(json.dumps({"loop": self.loop.currentText(), "tolerance": self.tolerance.value(),
                                            "alignment": self.alignment.currentText(), "notes": self.notes.toPlainText(),
                                            "results": self._measured}, default=str), parse_constant=lambda _: None)
            target.with_suffix(".json").write_text(json.dumps(clean, indent=2, allow_nan=False), encoding="utf-8")
            with target.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(("run", "aligned_seconds", "pv", "sp", "out", "quality"))
                for name, entry in self._measured.items():
                    for row in entry["aligned"]["plot"]:
                        writer.writerow((name, row["time"], row.get("pv"), row.get("sp"), row.get("out"), row.get("quality")))
            self.status.setText(f"Comparison report and data saved: {target}")
        except OSError as error:
            self.status.setText(f"Export failed: {error}")

    def _shutdown(self):
        self._timer.stop()
        # The chart retains service callbacks outside Qt's signal ownership.
        self.chart.context_services.clear()
        for future, _ in self._jobs:
            future.cancel()
        self._jobs.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def closeEvent(self, event):  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def done(self, result):
        self._shutdown()
        super().done(result)
