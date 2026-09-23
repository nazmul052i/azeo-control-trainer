"""Process History View for operator live and historical trend analysis."""
from __future__ import annotations

import logging
import math
from datetime import datetime

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
import weakref

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QHeaderView, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.pid.theme.colors import TEXT_SECONDARY

from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from .historian import MAX_CHART_PENS, PEN_COLOURS
from ..theme.widgets import bind_operator_theme
from ..theme.tokens import THEMES
from ..theme.roles import Role

log = logging.getLogger("hmi.history_view")

REFRESH_MS = 1000


class PointBrowserDialog(QDialog):
    """Searchable point picker; selection is capped by remaining pen slots."""

    def __init__(self, historian, remaining: int, parent=None):
        super().__init__(parent)
        self._historian = historian
        self._remaining = max(0, int(remaining))
        self.setWindowTitle("Add historian pens")
        self.resize(650, 470)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            f"Search configured history points (up to {self._remaining} more)"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by path, description, module or unit...")
        self._filter.textChanged.connect(self._populate)
        root.addWidget(self._filter)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        root.addWidget(self._list, 1)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        root.addWidget(self._status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._populate()
        bind_operator_theme(self, tool_controls=True)

    def apply_operator_theme(self, theme):
        self._status.setStyleSheet(f"color: {THEMES[theme][Role.TEXT_DIM]};")

    def _populate(self) -> None:
        needle = self._filter.text().strip().lower()
        self._list.clear()
        for path, point in sorted(self._historian.available_points().items()):
            haystack = " ".join((path, point.label, point.module, point.unit)).lower()
            if needle and needle not in haystack:
                continue
            item = QListWidgetItem(
                f"{point.legacy_path or point.path}    {point.label}    [{point.unit or '-'}]" +
                ("  · Retired history" if not point.active else ""))
            item.setData(Qt.UserRole, path)
            item.setToolTip(
                f"Module: {point.module or '-'}\nRange: {point.lo:g} to {point.hi:g}")
            self._list.addItem(item)
        self._status.setText(f"{self._list.count()} matching configured points")

    def selected_paths(self) -> list[str]:
        return [str(item.data(Qt.UserRole))
                for item in self._list.selectedItems()[:self._remaining]]


class ProcessHistoryView(QDialog):
    """Commercial operator trend: manage pens, compare scales and review data."""

    COLUMNS = (
        "", "Show", "Point", "Description", "Current", "Cursor",
        "Minimum", "Maximum", "Average", "Unit",
        "Quality", "Age", "A", "B", "Δ value", "Rate / s",
    )

    def __init__(self, historian, module: str = "", parent=None):
        super().__init__(parent)
        self.historian = historian
        self._chart = None
        self._pens: list[str] = []
        self._point_ids = {}
        self._point_refs = {}
        self._visible: dict[str, bool] = {}
        self._cursor_values: dict[str, float] = {}
        self._state_key = ""
        self._loading_table = False
        self._source = historian
        self._table_paths = ()
        self._cursor_time = None
        self._query_future = None
        self._query_serial = 0
        self._query_request = None
        self._pending_range = None
        self._event_rows = []
        self._event_stamp = None
        self._colours = {}
        self._pen_styles = {}
        self._closed = False
        self._exports = []
        self._export_jobs = []
        self._comparison_dialogs = []

        self.setWindowTitle("Process History View")
        self.setWindowFlags(
            Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.resize(1320, 820)
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self._build_ui()

        self._query_timer = QTimer(self)
        self._query_timer.setInterval(50)
        self._query_timer.timeout.connect(self._poll_query)
        self._query_timer.start()
        self._range_timer = QTimer(self)
        self._range_timer.setSingleShot(True)
        self._range_timer.setInterval(180)
        self._range_timer.timeout.connect(self._load_pending_range)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(REFRESH_MS)
        self._populate_modules()
        if module:
            self.show_module(module)
        self._restore_layout()
        bind_operator_theme(self, tool_controls=True)

    def apply_operator_theme(self, theme):
        p = THEMES[theme]
        self._title_label.setStyleSheet(
            f"font-size: 16pt; font-weight: bold; color: {p[Role.HEADING]};")
        if self._chart is not None:
            self._chart.apply_operator_theme(theme)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 10)
            if item is not None:
                item.setForeground(_colour(p[Role.TEXT] if item.text() == "GOOD" else p[Role.ALARM_P2_TEXT]))

    # --------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        from azeo_control_trainer.core.presentation.flow_layout import FlowLayout
        root = QVBoxLayout(self)
        # Expanded chart controls otherwise impose a native minimum size
        # before resizeEvent can switch to the compact layout (notably at
        # 200% desktop scaling). Keep the window resizable through that point.
        root.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(560, 400)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        heading = QHBoxLayout()
        title = self._title_label = QLabel("Process History")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #004487;")
        heading.addWidget(title)
        self._status = QLabel("Live collection")
        self._status.setWordWrap(True)
        heading.addWidget(self._status, 1)
        self._details = self._tool_button("Statistics", self._toggle_details)
        self._details.setCheckable(True)
        heading.addWidget(self._details)
        self._export_button = self._tool_button("Export…", self._export)
        heading.addWidget(self._export_button)
        root.addLayout(heading)

        bar = FlowLayout()
        bar.addWidget(QLabel("Module"))
        self._modules = AuthoringComboBox()
        self._modules.setMinimumWidth(210)
        self._modules.setEditable(True)
        self._modules.setInsertPolicy(QComboBox.NoInsert)
        self._modules.textActivated.connect(self.show_module)
        bar.addWidget(self._modules)
        self._add = self._tool_button("Add Pen...", self._browse_points)
        self._point_details = self._tool_button("Point configuration…", self.show_point_details)
        self._remove = self._tool_button("Remove", self.remove_selected_pen)
        self._up = self._tool_button("Up", lambda: self.move_selected_pen(-1))
        self._down = self._tool_button("Down", lambda: self.move_selected_pen(1))
        self._clear = self._tool_button("Clear", self.clear_pens)
        bar.addWidget(self._add)
        bar.addWidget(self._point_details)
        for button in (self._remove, self._up, self._down, self._clear):
            button.setParent(self)
            button.hide()
        self._groups = AuthoringComboBox()
        self._groups.setMinimumWidth(175)
        self._groups.setPlaceholderText("Saved trend groups")
        self._groups.activated.connect(self._load_group)
        bar.addWidget(self._groups)
        self._group_save = self._tool_button("Save group…", self._save_group)
        self._archive_button = self._tool_button("Archive…", self._browse_archive)
        self._compare_button = self._tool_button("Compare runs…", self._compare_runs)
        for button in (self._group_save, self._archive_button, self._compare_button):
            bar.addWidget(button)
        self._clock_mode = AuthoringComboBox()
        self._clock_mode.addItems(("Elapsed time", "Local time"))
        self._clock_mode.currentIndexChanged.connect(self._change_clock)
        bar.addWidget(self._clock_mode)
        self._more_button = self._tool_button("Tools…", self._compact_tools)
        bar.addWidget(self._more_button)
        self._more_button.hide()
        self._span = QLabel("")
        root.addLayout(bar)
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        try:
            from azeo_control_trainer.core.pid.charts.historian_trend import (
                HistorianTrendWidget,
            )
            self._chart = HistorianTrendWidget("Process History")
            self._chart.set_embedded_controls()
            self._chart.cursor_changed.connect(self._on_cursor_changed)
            self._chart.ab_changed.connect(self._update_ab)
            self._chart.live_changed.connect(self._live_changed)
            self._chart.review_range_changed.connect(self._range_changed)
            self._chart.navigation_started.connect(self._navigation_started)
            self._chart.window_changed.connect(self._window_changed)
            self._chart.event_selected.connect(self._inspect_event)
            self._chart.pen_changed.connect(self._chart_pen_changed)
            self._chart.interaction_error.connect(lambda message: self._status.setText(f"History action failed: {message}"))
            self._splitter.addWidget(self._chart)
        except Exception:  # noqa: BLE001
            log.exception("Trend chart unavailable")
            note = QLabel(
                "The chart could not be created; the historian table remains available.")
            note.setWordWrap(True)
            self._splitter.addWidget(note)

        self._tabs = QTabWidget()

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setMinimumHeight(132)
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.itemSelectionChanged.connect(self._update_button_state)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._pen_menu)
        header = self._table.horizontalHeader()
        # Resizing every column after every cursor value made a ten-pen
        # crosshair stall. Keep numeric columns stable; identity gets the space.
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultSectionSize(88)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 24)
        self._table.setColumnWidth(1, 56)
        self._table.setColumnWidth(9, 65)
        self._table.setColumnWidth(10, 85)
        self._table.setColumnWidth(11, 65)
        self._tabs.addTab(self._table, "Pens and values")
        self._events = QTableWidget(0, 5)
        self._events.setHorizontalHeaderLabels(("Time", "Category", "Event", "Equipment", "Evidence"))
        self._events.verticalHeader().hide()
        self._events.setAlternatingRowColors(True)
        self._events.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._events.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((105, 85, 210, 185)):
            self._events.setColumnWidth(column, width)
        self._events.horizontalHeader().setStretchLastSection(True)
        self._events.cellClicked.connect(self._select_event)
        self._tabs.addTab(self._events, "Events")
        self._splitter.addWidget(self._tabs)
        self._splitter.setSizes([550, 180])
        bottom = FlowLayout()
        bottom.addWidget(self._span)
        self._event_filter = AuthoringComboBox()
        self._event_filter.addItems(("All events", "alarm", "operator", "mode", "training", "clock", "note"))
        self._event_filter.currentIndexChanged.connect(lambda: self._update_events(force=True))
        bottom.addWidget(self._event_filter)
        self._note_button = self._tool_button("Bookmark / note…", self._add_note)
        bottom.addWidget(self._note_button)
        root.addLayout(bottom)
        self._reload_groups()
        if self._chart:
            self._chart.context_services = {
                "add_pen": lambda _: self._browse_points(),
                "remove_pen": self._remove_context_pen,
                "archive": lambda _: self._browse_archive(),
                "compare_runs": lambda _: self._compare_runs(),
                "save_group": lambda _: self._save_group(),
                "export": lambda _: self._export(),
                "export_ab": lambda _: self._export(scope=1),
                "bookmark": self._bookmark_at,
                "events": self._events_at,
                "statistics": lambda _: self._details.setChecked(True),
                "clock": lambda _: self._clock_mode.setCurrentIndex(1 - self._clock_mode.currentIndex()),
            }
        self._adapt_width()
        self._update_button_state()

    def _toggle_details(self):
        self._adapt_width()

    def _adapt_width(self):
        narrow = self.width() < 1000 or self.height() < 620
        if self._chart is not None:
            self._chart.set_compact_layout(narrow)
            self._chart._plot_widget.setMinimumHeight(
                max(80, min(180 if narrow else 220, self.height() - 350)))
        for control in (self._groups, self._group_save, self._archive_button, self._compare_button,
                        self._clock_mode, self._note_button):
            control.setVisible(not narrow)
        self._more_button.setVisible(narrow)
        for column in (3, 6, 7, 8):
            self._table.setColumnHidden(column, not self._details.isChecked())
        for column in (12, 13, 14, 15):
            self._table.setColumnHidden(column, self._chart is None or not self._chart._ab_button.isChecked())
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Interactive if narrow else QHeaderView.Stretch)
        if narrow:
            self._table.setColumnWidth(2, 210)
            self._table.setColumnHidden(11, True)
        else:
            self._table.setColumnHidden(11, False)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            self._adapt_width()

    def heightForWidth(self, width):  # noqa: N802
        # QScrollArea otherwise treats the plot's preferred height as a
        # requirement and pushes the value table below a perfectly usable pane.
        return max(self.minimumHeight(), self.layout().minimumHeightForWidth(width))

    @staticmethod
    def _tool_button(text: str, callback) -> QPushButton:
        button = QPushButton(text)
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon
        name = next((icon for prefix, icon in (("Add", "new"), ("Save", "save"), ("Archive", "history"),
                                                ("Compare", "compare"), ("Bookmark", "comment"),
                                                ("Statistics", "values"), ("Export", "datalog"))
                     if text.startswith(prefix)), None)
        if name:
            button.setProperty("operator_icon", name)
            button.setIcon(studio_icon(name, 17))
        button.clicked.connect(callback)
        return button

    def _populate_modules(self) -> None:
        modules = sorted({point.module for point in self.historian.TAGS.values()
                          if point.module})
        self._modules.blockSignals(True)
        self._modules.clear()
        self._modules.addItems(modules)
        self._modules.blockSignals(False)
        if modules and not self._pens:
            self.show_module(modules[0])

    # ------------------------------------------------------------ pens
    def show_module(self, module: str) -> None:
        if not module:
            return
        requested_key = f"module:{module}"
        if requested_key == self._state_key:
            return
        self._save_state()
        self._select_module_label(module)
        self._state_key = requested_key
        state = self.historian.chart_state(requested_key)
        if state:
            self._colours.update(state.get("colours", {}))
            self._pen_styles.update(state.get("pen_styles", {}))
            self._pens = [path for path in state.get("pens", [])
                          if path in self.historian.TAGS][:MAX_CHART_PENS]
            self._visible = {
                path: bool(state.get("visible", {}).get(path, True))
                for path in self._pens
            }
        else:
            self._pens = self.historian.default_pens_for(module)
            self._visible = {path: True for path in self._pens}
        self._apply_pens()
        if state and self._chart is not None:
            self._chart.set_time_window(float(state.get("window", 10.0)))
            self._chart.set_normalized(bool(state.get("normalized", False)))
        self._refresh()

    def show_point(self, path: str) -> bool:
        point = self.historian.TAGS.get(path)
        if point is None:
            return False
        self._save_state()
        self._state_key = "custom"
        self._select_module_label(point.module)
        self._pens = [path]
        self._visible = {path: True}
        self._apply_pens()
        self._refresh()
        return True

    def add_point(self, path: str) -> bool:
        self._sync_point_identities()
        if path not in self.historian.TAGS:
            return False
        if path in self._pens:
            return True
        if len(self._pens) >= MAX_CHART_PENS:
            return False
        point = self.historian.TAGS[path]
        existing_modules = {
            self.historian.TAGS[pen].module for pen in self._pens
            if pen in self.historian.TAGS
        }
        if existing_modules and point.module not in existing_modules:
            self._save_state()
            self._state_key = "custom"
        self._pens.append(path)
        self._visible[path] = True
        self._update_module_label_for_pens()
        self._apply_pens()
        self._refresh()
        return True

    def set_pens(self, paths: list[str]) -> None:
        """Set a validated, unique pen list; useful to station launchers too."""
        self._save_state()
        self._state_key = "custom"
        self._pens = list(dict.fromkeys(
            path for path in paths if path in self.historian.TAGS
        ))[:MAX_CHART_PENS]
        self._visible = {path: self._visible.get(path, True) for path in self._pens}
        self._update_module_label_for_pens()
        self._apply_pens()
        self._refresh()

    def _browse_points(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless() or len(self._pens) >= MAX_CHART_PENS:
            return
        dialog = PointBrowserDialog(
            self.historian, MAX_CHART_PENS - len(self._pens), self)
        if dialog.exec() != QDialog.Accepted:
            return
        for path in dialog.selected_paths():
            self.add_point(path)

    def remove_selected_pen(self) -> bool:
        row = self._table.currentRow()
        if not 0 <= row < len(self._pens):
            return False
        removed = self._pens.pop(row)
        self._visible.pop(removed, None)
        self._update_module_label_for_pens()
        self._apply_pens()
        self._refresh()
        if self._pens:
            self._table.selectRow(min(row, len(self._pens) - 1))
        return True

    def clear_pens(self) -> None:
        self._pens.clear()
        self._visible.clear()
        self._select_module_label("")
        self._apply_pens()
        self._refresh()

    def move_selected_pen(self, delta: int) -> bool:
        row = self._table.currentRow()
        target = row + int(delta)
        if not (0 <= row < len(self._pens) and 0 <= target < len(self._pens)):
            return False
        self._pens[row], self._pens[target] = self._pens[target], self._pens[row]
        self._apply_pens()
        self._refresh()
        self._table.selectRow(target)
        return True

    def _apply_pens(self) -> None:
        self._point_ids = {path: self._point_ids.get(path) or self.historian.TAGS[path].point_id
                           for path in self._pens if path in self.historian.TAGS}
        self._point_refs = {path: self._point_refs.get(path) or self.historian.TAGS[path]
                           for path in self._pens if path in self.historian.TAGS}
        if self._chart is None:
            return
        from azeo_control_trainer.core.pid.charts.historian_trend import TrendPen
        normalized = self._chart.is_normalized()
        live = self._chart.is_live()
        interval = self._chart.visible_time_range()
        self._chart.remove_all_pens()
        self._source = self.historian
        self._query_serial += 1
        self._event_stamp = None
        units = list(dict.fromkeys(self.historian.TAGS[p].unit for p in self._pens))
        for index, path in enumerate(self._pens):
            point = self.historian.TAGS.get(path)
            if point is None:
                continue
            used = {self._colours[p] for p in self._pens if p != path and p in self._colours}
            colour = self._colours.get(path)
            if not colour:
                colour = next((value for value in PEN_COLOURS if value not in used), PEN_COLOURS[index % len(PEN_COLOURS)])
            self._colours[path] = colour
            secondary = point.unit == "%" and any(unit != "%" for unit in units)
            if len(units) == 2 and "%" not in units:
                secondary = point.unit == units[1]
            self._chart.add_pen(TrendPen(
                path, point.label or path, point.unit, colour,
                point.lo, point.hi, visible=self._visible.get(path, True),
                axis="right" if secondary else "left",
                line_width=self._pen_styles.get(path, {}).get("line_width", 1.5),
                line_style=self._pen_styles.get(path, {}).get("line_style", "dash" if path.endswith("/SP") else "solid")))
            self._chart.set_pen_visible(path, self._visible.get(path, True))
        if normalized != self._chart.is_normalized():
            self._chart.set_normalized(normalized)
        if len(units) > 2:
            self._chart.set_normalized(True)
            self._status.setText("Multiple engineering units · comparison uses configured span (%)")
        if self.historian.archive is not None and hasattr(self, "_range_timer"):
            now = self.historian.now() / 60
            start, end = (max(0, now - min(self._chart.time_window_min(), 10080)), now) if live else interval
            if start is not None and end is not None:
                self._queue_query(start, end, live=live)

    def _chart_pen_changed(self, path):
        pen = self._chart._pens.get(path)
        if pen is None:
            return
        self._visible[path] = pen.visible
        self._colours[path] = pen.color
        self._pen_styles[path] = {"line_style": pen.line_style, "line_width": pen.line_width}
        self._update_table()
        self._save_state()

    def _remove_context_pen(self, path):
        if path in self._pens:
            self._table.selectRow(self._pens.index(path))
            self.remove_selected_pen()

    def _bookmark_at(self, time_min):
        self._chart.focus_time(time_min)
        self._cursor_time = time_min
        self._add_note()

    def _events_at(self, time_min):
        self._chart.focus_time(time_min)
        self._tabs.setCurrentIndex(1)
        self._update_events(force=True)

    def _select_module_label(self, module: str) -> None:
        self._modules.blockSignals(True)
        if module:
            index = self._modules.findText(module)
            if index < 0:
                self._modules.addItem(module)
                index = self._modules.findText(module)
            self._modules.setCurrentIndex(index)
        else:
            self._modules.setPlaceholderText("Selected points")
            self._modules.setCurrentIndex(-1)
        self._modules.blockSignals(False)

    def _update_module_label_for_pens(self) -> None:
        modules = {self.historian.TAGS[path].module for path in self._pens
                   if path in self.historian.TAGS}
        self._select_module_label(next(iter(modules)) if len(modules) == 1 else "")

    # ---------------------------------------------------------- refresh
    def _refresh(self) -> None:
        if self._closed:
            return
        self._sync_point_identities()
        self.historian.collect()
        if self._source is not self.historian and self._chart is not None and self._chart.is_live():
            self._source.merge_live(self.historian)
        if self._chart is not None:
            try:
                self._chart.update_all(self._source)
            except Exception:  # noqa: BLE001
                log.exception("Trend refresh failed")
        self._update_table()
        self._update_events()
        span = self._source.span_s()
        archive = self.historian.archive
        retention = f" · Disk: ≤{archive.retention_days} d / {archive.storage_limit_mb} MB" if archive else " · Memory history"
        self._span.setText(
            f"{len(self._pens)}/{MAX_CHART_PENS} pens · {span / 60.0:.1f} min loaded{retention}")
        if archive is not None and archive.error:
            self._status.setText(f"Archive needs attention: {archive.error}")

    def _update_table(self) -> None:
        start, end = ((None, None) if self._chart is None
                      else self._chart.visible_time_range())
        self._loading_table = True
        self._table.blockSignals(True)
        if tuple(self._pens) != self._table_paths:
            self._table.setRowCount(len(self._pens))
            for row in range(len(self._pens)):
                for column in range(len(self.COLUMNS)):
                    self._table.setItem(row, column, QTableWidgetItem())
            self._table_paths = tuple(self._pens)
        for row, path in enumerate(self._pens):
            point = self.historian.TAGS.get(path)
            if point is None:
                continue
            self._table.item(row, 0).setBackground(_colour(self._colours.get(path, point.colour)))
            shown = self._table.item(row, 1)
            shown.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                           | Qt.ItemIsUserCheckable)
            shown.setCheckState(
                Qt.Checked if self._visible.get(path, True) else Qt.Unchecked)
            shown.setData(Qt.UserRole, path)
            self._set_cell(row, 2, (point.legacy_path or point.path) + (" · retired" if not point.active else ""))
            self._set_cell(row, 3, point.label)
            stats = self._source.statistics(path, start, end)
            latest = self.historian.latest(path)
            values = (
                latest["value"] if latest["quality"] in {"GOOD", "UNCERTAIN"} else math.nan,
                self._cursor_values.get(path, math.nan),
                stats["minimum"], stats["maximum"], stats["average"],
            )
            for column, value in zip(range(4, 9), values):
                item = self._table.item(row, column)
                self._set_cell(row, column, _format_value(value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            plotted = self._source.TAGS.get(path, point)
            self._set_cell(row, 9, plotted.unit)
            # A review may use older engineering units; never let its unit
            # column relabel the independently collected current value.
            if point.unit != plotted.unit or plotted.metadata_conflict:
                self._set_cell(row, 4, _format_value(values[0]) + " " + (point.unit or "unitless"))
            self._set_cell(row, 10, latest["quality"])
            self._set_cell(row, 11, f"{latest['age']:.1f} s" if latest["age"] is not None else "—")
            last_good = latest["last_good"]
            self._table.item(row, 4).setToolTip(
                f"Latest collected value ({point.unit or 'unitless'}), independent of the review window.\nLast good: " +
                (datetime.fromtimestamp(last_good).astimezone().isoformat(timespec="seconds") if last_good else "not recorded"))
            theme = getattr(self, "_hmi_theme_name", None)
            color = (THEMES[theme][Role.ALARM_P2_TEXT if latest["quality"] != "GOOD" else Role.TEXT]
                     if theme else "#9C3D10" if latest["quality"] != "GOOD" else "#2B323B")
            self._table.item(row, 10).setForeground(_colour(color))
        self._table.blockSignals(False)
        self._loading_table = False
        self._update_button_state()

    def _on_cursor_changed(self, _time_min: float, values: dict) -> None:
        self._cursor_time = _time_min
        self._cursor_values = dict(values)
        records = self._source.nearest_values(self._pens, _time_min)
        for row, path in enumerate(self._pens):
            if self._table.item(row, 5) is None:
                continue
            record = records.get(path)
            value = record[0] if record else math.nan
            self._set_cell(row, 5, _format_value(value))
            self._table.item(row, 5).setToolTip(
                f"{record[1]} · actual sample {record[2]:.5f} min" if record else
                "Zoom in to load raw samples at this time" if self._source.reduced else "No sample at this time")

    def _set_cell(self, row, column, text):
        item = self._table.item(row, column)
        if item is not None and item.text() != str(text):
            item.setText(str(text))

    def _update_ab(self, a, b):
        self._adapt_width()
        av = self._source.nearest_values(self._pens, a)
        bv = self._source.nearest_values(self._pens, b)
        dt = (b - a) * 60
        for row, path in enumerate(self._pens):
            first, second = av.get(path), bv.get(path)
            good = first and second and first[1] == second[1] == "GOOD"
            delta = second[0] - first[0] if good else math.nan
            for column, value in ((12, first[0] if first else math.nan), (13, second[0] if second else math.nan),
                                  (14, delta), (15, delta / dt if abs(dt) > 1e-9 else math.nan)):
                self._set_cell(row, column, _format_value(value))

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table or item.column() != 1:
            return
        path = item.data(Qt.UserRole)
        if path not in self._pens:
            return
        visible = item.checkState() == Qt.Checked
        self._visible[path] = visible
        if self._chart is not None:
            self._chart.set_pen_visible(path, visible)

    def _update_button_state(self) -> None:
        row = self._table.currentRow()
        selected = 0 <= row < len(self._pens)
        self._remove.setEnabled(selected)
        self._up.setEnabled(selected and row > 0)
        self._down.setEnabled(selected and row < len(self._pens) - 1)
        self._clear.setEnabled(bool(self._pens))
        self._add.setEnabled(len(self._pens) < MAX_CHART_PENS)

    # ----------------------------------------------------------- state
    def _sync_point_identities(self):
        mapped = {path: self.historian.point_key(identity) or path
                  for path, identity in self._point_ids.items() if identity}
        mapped.update({path: point.path for path, point in self._point_refs.items() if point.legacy_path})
        if any(old != new for old, new in mapped.items()):
            self._pens = [mapped.get(path, path) for path in self._pens]
            for name in ("_visible", "_colours", "_pen_styles", "_point_ids", "_point_refs"):
                setattr(self, name, {mapped.get(path, path): value
                                     for path, value in getattr(self, name).items()})
            self._cursor_values.clear()
            self._update_module_label_for_pens()
            self._apply_pens()

    def _save_state(self) -> None:
        self._sync_point_identities()
        if not self._state_key:
            return
        state = {
            "pens": list(self._pens),
            "visible": dict(self._visible),
            "window": self._chart.time_window_min() if self._chart else 10.0,
            "normalized": self._chart.is_normalized() if self._chart else False,
            "colours": dict(self._colours), "pen_styles": dict(self._pen_styles),
        }
        self.historian.save_chart_state(self._state_key, state)

    def _layout_state(self):
        return {"size": [self.width(), self.height()], "split": self._splitter.sizes(),
                "statistics": self._details.isChecked(), "clock": self._clock_mode.currentIndex()}

    def _restore_layout(self):
        state = self.historian.chart_state("window:history") or {}
        if state.get("size"):
            width, height = state["size"]
            self.resize(min(1800, max(740, int(width))), min(1100, max(560, int(height))))
        self._splitter.setSizes(state.get("split", [550, 180]))
        self._details.setChecked(bool(state.get("statistics", False)))
        self._clock_mode.setCurrentIndex(int(state.get("clock", 0)))
        self._adapt_width()

    def _reload_groups(self):
        if not hasattr(self, "_groups"):
            return
        self._groups.clear()
        self._groups.addItem("Saved trend groups", None)
        for key, state in sorted(self.historian._chart_states.items(),
                                 key=lambda pair: (not pair[1].get("favorite", False), pair[0])):
            if key.startswith("group:"):
                self._groups.addItem(("★ " if state.get("favorite") else "") + key[6:], key)

    def _save_group(self, name=None):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not isinstance(name, str):
            if is_headless():
                return
            name, accepted = QInputDialog.getText(self, "Save trend group", "Name")
            if not accepted:
                return
        if not name.strip():
            return
        state = {"pens": list(self._pens), "visible": dict(self._visible),
                 "window": self._chart.time_window_min(), "normalized": self._chart.is_normalized(),
                 "colours": dict(self._colours), "pen_styles": dict(self._pen_styles),
                 "favorite": True, "layout": self._layout_state()}
        self.historian.save_chart_state("group:" + name.strip()[:80], state)
        self._reload_groups()
        self._status.setText(f"Saved favorite trend group: {name.strip()}")

    def _load_group(self, index):
        key = self._groups.itemData(index)
        state = self.historian.chart_state(key) if key else None
        if not state:
            return
        self._colours.update(state.get("colours", {}))
        self._pen_styles.update(state.get("pen_styles", {}))
        self.set_pens(state.get("pens", []))
        self._visible.update(state.get("visible", {}))
        self._chart.set_time_window(state.get("window", 10))
        self._chart.set_normalized(state.get("normalized", False))
        for path in self._pens:
            self._chart.set_pen_visible(path, self._visible.get(path, True))
        if state.get("layout"):
            self._splitter.setSizes(state["layout"].get("split", [550, 180]))
        self._refresh()
        self._status.setText(f"Trend group: {key[6:]}")

    def _pen_menu(self, position):
        from azeo_control_trainer.core.presentation.menu_style import studio_menu
        index = self._table.indexAt(position)
        if index.isValid():
            self._table.selectRow(index.row())
            path = self._pens[index.row()]
        else:
            path = None
        previous = getattr(self, "_pen_context_menu", None)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        menu = studio_menu(parent=self)
        self._pen_context_menu = menu

        def invoke(button=None):
            if path not in self._pens:
                return
            self._table.selectRow(self._pens.index(path))
            if button is None:
                self.show_point_details()
            elif button.isEnabled():
                button.click()

        for button in (self._add, self._remove, self._up, self._down, self._clear):
            action = menu.addAction(button.text())
            targeted = button in (self._remove, self._up, self._down)
            action.setEnabled(button.isEnabled() and (path is not None or not targeted))
            action.triggered.connect(
                (lambda _checked=False, button=button: invoke(button)) if targeted else button.click)
        detail = menu.addAction("Point configuration and recorded releases…", lambda: invoke())
        detail.setEnabled(path is not None)
        group = self._groups.currentData()
        if group:
            menu.addSeparator()
            action = menu.addAction("Toggle favorite for this group")
            action.triggered.connect(lambda: self._toggle_favorite(group))
        menu.popup(self._table.viewport().mapToGlobal(position))

    def show_point_details(self):
        self._sync_point_identities()
        if not self._pens:
            return None
        path = self._pens[max(0, self._table.currentRow())]
        start, end = self._chart.visible_time_range()
        start = 0 if start is None else start
        end = self.historian.now() / 60 if end is None else end
        from .point_details import PointDetails
        dialog = PointDetails(self.historian, path, start, end, self)
        self._retain_comparison(dialog)
        dialog.show()
        return dialog

    def _compact_tools(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        menu = QMenu(self)
        for button in (self._group_save, self._archive_button, self._compare_button, self._note_button):
            menu.addAction(button.icon(), button.text(), button.click)
        groups = menu.addMenu("Saved trend groups")
        for index in range(1, self._groups.count()):
            action = groups.addAction(self._groups.itemText(index))
            action.triggered.connect(lambda _=False, i=index: self._load_group(i))
        clock = menu.addAction("Show local time")
        clock.setCheckable(True)
        clock.setChecked(bool(self._clock_mode.currentIndex()))
        clock.triggered.connect(lambda checked: self._clock_mode.setCurrentIndex(int(checked)))
        menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))

    def _toggle_favorite(self, key):
        state = self.historian.chart_state(key)
        if state:
            state["favorite"] = not state.get("favorite", False)
            self.historian.save_chart_state(key, state)
            self._reload_groups()

    def _change_clock(self, index):
        if self._chart is not None:
            self._chart.set_clock_axis(self.historian.origin if index else None)

    def _queue_query(self, start, end, *, live=False):
        if self.historian.archive is None or not self._pens:
            return
        self._query_serial += 1
        self._cancel_query()
        try:
            self._query_future = self.historian.query_async(self._pens, start, end, as_snapshot=True)
            self._query_request = (self._query_serial, start, end, live)
            self._status.setText("Loading archived history… live collection continues")
        except Exception as error:  # noqa: BLE001 - archive failure must not close a Qt window
            self._status.setText(f"History could not be loaded: {error}")
            log.exception("History query failed")

    def _cancel_query(self):
        if self._query_future is not None:
            archive = self.historian.archive
            if archive is not None and hasattr(archive, "cancel_query"):
                archive.cancel_query(self._query_future)
            else:
                self._query_future.cancel()

    def _poll_query(self):
        if self._closed:
            return
        if self._export_jobs:
            from .workspace_tools import poll_export_jobs
            poll_export_jobs(self)
        if self._query_future is not None and self._query_future.done():
            future, request = self._query_future, self._query_request
            self._query_future = None
            if request[0] != self._query_serial or future.cancelled():
                return
            try:
                self._source = future.result()
                if request[3] and self._chart.is_live():
                    self._source.merge_live(self.historian)
                self._event_stamp = None
                for path, pen in self._chart._pens.items():
                    point = self._source.TAGS.get(path)
                    if point:
                        pen.unit, pen.y_lo, pen.y_hi = point.unit, point.lo, point.hi
                self._chart.update_all(self._source)
                if not request[3]:
                    self._chart.set_review_range(request[1], request[2])
                reduced = " · Peak envelope; zoom in for cursor measurements. Exports use raw samples." if self._source.reduced else ""
                mixed = any(point.metadata_conflict for point in self._source.available_points().values())
                self._status.setText("Units changed: open Point configuration and select an interval with one unit. Mixed-unit traces are hidden."
                                     if mixed else ("Live + archived history" if request[3] else "Historical review") + reduced)
                self._update_table()
                self._update_events(force=True)
            except Exception as error:  # noqa: BLE001
                self._status.setText(f"History could not be loaded: {error}")
                log.exception("History result could not be displayed")
        for future, target in list(self._exports):
            if future.done():
                self._exports.remove((future, target))
                try:
                    future.result()
                    self._status.setText(f"Export saved: {target}")
                except Exception as error:  # noqa: BLE001
                    self._status.setText(f"Export failed: {error}")
                    log.exception("History export failed")

    def _navigation_started(self):
        # A completed older archive query must not snap the plot back while
        # the operator is still drawing a rectangle or panning.
        self._query_serial += 1
        self._cancel_query()
        self._pending_range = None
        self._range_timer.stop()

    def _range_changed(self, start, end):
        # Supersede immediately, including during the range debounce; a ready
        # older query must not replace the interval the operator just chose.
        self._query_serial += 1
        self._cancel_query()
        self._pending_range = (start, end)
        self._range_timer.start()

    def _window_changed(self, minutes):
        if not hasattr(self, "_range_timer"):
            return
        if self._chart.is_live():
            now = self.historian.now() / 60
            self._queue_query(max(0, now - min(minutes, 10080)), now, live=True)
        else:
            start, end = self._chart.visible_time_range()
            if start is not None and end is not None:
                self._range_changed(start, end)

    def _load_pending_range(self):
        if self._pending_range:
            start, end = self._pending_range
            self._pending_range = None
            if self.historian.archive:
                self._queue_query(start, end)
            else:
                self._update_table()
                self._update_events(force=True)

    def _live_changed(self, live):
        if not hasattr(self, "_range_timer"):
            return
        self._status.setText("Live collection" if live else "Review · collection continues")
        if live:
            self._range_timer.stop()
            self._source = self.historian
            self._query_serial += 1
            self._event_stamp = None
            now = self.historian.now() / 60
            self._queue_query(max(0, now - min(self._chart.time_window_min(), 10080)), now, live=True)

    def _browse_archive(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from .workspace_tools import ArchiveRangeDialog
        dialog = ArchiveRangeDialog(self.historian, self)
        if dialog.exec() == QDialog.Accepted:
            start, end = dialog.interval()
            if end <= start:
                self._status.setText("Choose an end time after the start time")
                return
            if self.historian.archive:
                try:
                    self.historian.archive.set_retention(dialog.retention.value(), dialog.budget.value())
                except (RuntimeError, OSError) as error:
                    self._status.setText(f"Retention could not be saved: {error}")
                    log.exception("History retention update failed")
                    return
                self._queue_query(start, end)
            self._chart.set_review_range(start, end)
            self._update_table()

    def _update_events(self, force=False):
        if not hasattr(self, "_event_filter") or self._chart is None:
            return
        start, end = self._chart.visible_time_range()
        category = self._event_filter.currentText()
        rows = sorted((r for r in self._source.events
                       if (category == "All events" or r["category"] == category)
                       and (start is None or r["time"] >= start * 60)
                       and (end is None or r["time"] <= end * 60)), key=lambda r: (r["time"], r["id"]))[-500:]
        stamp = tuple(row["id"] for row in rows)
        if not force and stamp == self._event_stamp:
            return
        self._event_stamp = stamp
        self._event_rows = rows
        self._events.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (datetime.fromtimestamp(row["wall_time"]).strftime("%H:%M:%S"), row["category"],
                      row["action"], row.get("target", ""), str(row.get("detail", "")))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"Observed at {row['wall_time']:.3f} · simulation {row.get('sim_time')} s\n{row.get('detail', '')}")
                self._events.setItem(index, column, item)
        self._tabs.setTabText(1, f"Events ({len(rows)})")
        self._chart.set_events(rows)

    def _select_event(self, row, _column):
        if 0 <= row < len(self._event_rows):
            self._inspect_event(self._event_rows[row])

    def _inspect_event(self, row):
        centre = row["time"] / 60
        self._chart.focus_time(centre)
        self._chart.set_review_range(centre - 1, centre + 1)
        self._queue_query(centre - 1, centre + 1)
        self._status.setText(f"{row['action']} · {row.get('target', '')} · observed event time")

    def _add_note(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        note, accepted = QInputDialog.getMultiLineText(self, "History bookmark", "Observation / instructor note")
        if accepted and note.strip():
            t = self._cursor_time * 60 if self._cursor_time is not None else self.historian.now()
            row = self.historian.add_event("note", "Bookmark", ", ".join(self._pens), note.strip(), t=t)
            if self._source is not self.historian:
                self._source.events.append(row)
            self._update_events(force=True)
            self._tabs.setCurrentIndex(1)

    def _compare_runs(self):
        from .workspace_tools import RunComparisonDialog
        dialog = RunComparisonDialog(self.historian, self)
        self._retain_comparison(dialog)
        dialog.show()

    def _retain_comparison(self, dialog):
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._comparison_dialogs.append(dialog)
        owner_ref, dialog_ref = weakref.ref(self), weakref.ref(dialog)

        def forgotten(*_):
            owner, old = owner_ref(), dialog_ref()
            if owner is not None and old in owner._comparison_dialogs:
                owner._comparison_dialogs.remove(old)

        dialog.destroyed.connect(forgotten)

    # ---------------------------------------------------------- export
    def _export(self, _checked=False, *, scope=0) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        from .workspace_tools import ExportHistoryDialog
        dialog = ExportHistoryDialog(self)
        dialog.scope.setCurrentIndex(scope)
        if dialog.exec() == QDialog.Accepted:
            try:
                future, target = dialog.export()
                if future is not None:
                    self._exports.append((future, target))
                    self._status.setText("Exporting history in the background…")
                else:
                    self._status.setText(f"Export saved: {target}")
            except Exception as error:  # noqa: BLE001
                self._status.setText(f"Export failed: {error}")
                log.exception("History export could not start")

    def _shutdown(self):
        if self._closed:
            return
        self._save_state()
        self.historian.save_chart_state("window:history", self._layout_state())
        self._closed = True
        self._query_serial += 1
        self._cancel_query()
        self._timer.stop()
        self._query_timer.stop()
        self._range_timer.stop()
        for dialog in list(self._comparison_dialogs):
            dialog.close()
        for job in self._export_jobs:
            job["future"].cancel()
            job["completion"].cancel()
        self._export_jobs.clear()

    def closeEvent(self, event):  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def done(self, result):
        self._shutdown()
        super().done(result)


def _format_value(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "----"
    if not math.isfinite(number):
        return "----"
    decimals = 6 if 0 < abs(number) < 1 else 3
    return f"{number:,.{decimals}f}".rstrip("0").rstrip(".")


def _colour(name: str):
    from PySide6.QtGui import QColor
    return QColor(name)
