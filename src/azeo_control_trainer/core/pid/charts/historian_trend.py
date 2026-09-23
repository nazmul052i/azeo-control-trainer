"""Operator historian chart with Azeo-style live and review workflows.

The widget is shared by faceplates and Process History View. It owns chart
interaction and rendering only; point discovery, collection and session chart
configuration remain in :mod:`azeo_control_trainer.core.hmi.history`.
"""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QMenu, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from ..theme.colors import (
    BG_PANEL, BG_TREND, DV_ALARM_BAR1, DV_PV_FG, TEXT_ON_DARK,
)
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox

log = logging.getLogger(__name__)

pg.setConfigOptions(
    antialias=True, background=BG_TREND, foreground=DV_ALARM_BAR1)

MAX_PENS = 10
_WINDOWS = {
    "1 min": 1.0, "2 min": 2.0, "5 min": 5.0, "10 min": 10.0,
    "20 min": 20.0, "30 min": 30.0, "1 hr": 60.0, "2 hr": 120.0,
    "8 hr": 480.0, "24 hr": 1440.0, "7 days": 10080.0, "All": 1e9,
}


@dataclass
class TrendPen:
    """Configuration and runtime curve for one historian trace."""

    tag: str
    label: str
    unit: str
    color: str
    y_lo: float = 0.0
    y_hi: float = 100.0
    line_width: float = 1.5
    visible: bool = True
    axis: str = "left"
    _curve: Optional[pg.PlotDataItem] = None
    line_style: str = "solid"


class HistoryTimeAxis(pg.AxisItem):
    def __init__(self):
        super().__init__(orientation="bottom")
        self.origin = None

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        if self.origin is None:
            return super().tickStrings(values, scale, spacing)
        pattern = "%d %b %H:%M" if spacing >= 60 else "%H:%M:%S"
        return [datetime.fromtimestamp(self.origin + value * 60).strftime(pattern) for value in values]


class HistorianTrendWidget(QWidget):
    """Multi-pen process trend with live, review and comparison modes."""

    time_selected = Signal(float)
    cursor_changed = Signal(float, object)
    live_changed = Signal(bool)
    review_range_changed = Signal(float, float)
    navigation_started = Signal()
    ab_changed = Signal(float, float)
    event_selected = Signal(object)
    window_changed = Signal(float)
    pen_changed = Signal(str)
    interaction_error = Signal(str)

    def __init__(self, title: str = "Process Trend", parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._pens: dict[str, TrendPen] = {}
        self._data_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._pen_checkboxes: dict[str, QCheckBox] = {}
        self._pen_value_labels: dict[str, QLabel] = {}
        self._legend_rows: dict[str, QWidget] = {}
        self._live_mode = True
        self._normalized_mode = False
        self._time_window_min = 10.0
        self._data_versions = {}
        self._qualities = {}
        self._historian = None
        self._event_items = []
        self._legend_enabled = True
        self._recorded_mode = False
        self.context_services = {}
        self._context_menu = None
        self._grid_visible = True
        self._events_visible = True
        self._navigation_history = []
        self._build_ui()
        self._menu_shortcuts = []
        for key in ("Shift+F10", "Menu"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda: self.run_context_action(
                lambda _: self.open_context_menu(self._plot_widget.viewport().rect().center())))
            self._menu_shortcuts.append(shortcut)

    def apply_operator_theme(self, theme):
        """Change plot furniture without touching samples, ranges, or cursors."""
        from azeo_control_trainer.core.hmi.theme.widgets import apply_widget_theme, TOOL_METRICS
        from azeo_control_trainer.core.hmi.theme.roles import Role
        from azeo_control_trainer.core.hmi.theme.tokens import THEMES
        p = THEMES[theme]
        apply_widget_theme(self, theme, extra=TOOL_METRICS)
        self.toolbar.setStyleSheet("FlowBar { border: none; padding: 2px; }")
        self._legend_scroll.setStyleSheet("")
        self._btn_live.setStyleSheet("")
        self._plot_widget.setBackground(p[Role.SURFACE_SUNK])
        self._plot.titleLabel.setText(self._title, color=p[Role.TEXT])
        for side in ("left", "right", "top", "bottom"):
            axis = self._plot.getAxis(side)
            axis.setPen(pg.mkPen(p[Role.LINE]))
            axis.setTextPen(pg.mkPen(p[Role.TEXT_DIM]))
            axis.setLabel(**dict(axis.labelStyle, color=p[Role.TEXT_DIM]))
        for line in (self._vline, self._hline):
            line.setPen(pg.mkPen(p[Role.TEXT_DIM], style=Qt.DashLine))
        for line, role in ((self._a_line, Role.TEXT), (self._b_line, Role.ACTION)):
            line.setPen(pg.mkPen(p[role], width=1.5))
            line.label.setColor(p[role])
        self._cursor_label.setColor(p[Role.TEXT])
        self._cursor_label.fill = pg.mkBrush(p[Role.SURFACE_PANEL_ALT])
        for pen in self._pens.values():
            self._theme_legend(pen)
        self._plot_widget.viewport().update()

    def _theme_legend(self, pen):
        theme = getattr(self, "_hmi_theme_name", None)
        if not theme:
            return
        from azeo_control_trainer.core.hmi.theme.roles import Role
        from azeo_control_trainer.core.hmi.theme.tokens import THEMES
        from azeo_control_trainer.core.hmi.theme.vision import luminance
        p = THEMES[theme]
        light, dark = sorted((luminance(pen.color), luminance(p[Role.SURFACE_SUNK])), reverse=True)
        pen._curve.setShadowPen(pg.mkPen(p[Role.TEXT], width=pen.line_width + 2)
                                if (light + 12.75) / (dark + 12.75) < 3 else None)
        self._pen_value_labels[pen.tag].setStyleSheet(
            f"color: {p[Role.TEXT]}; background: {p[Role.SURFACE_FIELD]};"
            "font-family: Consolas; padding: 1px 4px;")
        self._pen_checkboxes[pen.tag].setStyleSheet(
            f"QCheckBox::indicator:checked {{ background: {pen.color}; border: 1px solid {p[Role.TEXT]}; }}")

    # --------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        from azeo_control_trainer.core.presentation.flow_layout import FlowBar
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)
        self.setStyleSheet(AUTHORING_CHROME_QSS)

        toolbar = FlowBar()
        self.toolbar = toolbar
        toolbar.setStyleSheet(
            f"FlowBar {{ background: {BG_PANEL}; border: none; padding: 2px; }}")
        toolbar.addWidget(QLabel(" Window "))
        self._window_combo = AuthoringComboBox()
        for label, minutes in _WINDOWS.items():
            self._window_combo.addItem(label, minutes)
        self._window_combo.setCurrentText("10 min")
        # The shared field reserves space for its arrow and padding. A fixed
        # 76px box could leave a negative text rect and paint no range at all.
        self._window_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._window_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._window_combo.setAccessibleName("Trend time window")
        self._window_combo.currentTextChanged.connect(self._on_window_change)
        toolbar.addWidget(self._window_combo)
        toolbar.addSeparator()

        self._btn_live = QPushButton("Live")
        self._btn_live.setCheckable(True)
        self._btn_live.setChecked(True)
        self._btn_live.setToolTip("Follow the newest historian samples")
        self._btn_live.setStyleSheet(
            f"QPushButton:checked {{ background: {DV_ALARM_BAR1};"
            f" color: {TEXT_ON_DARK}; border: 1px solid {DV_PV_FG};"
            " font-weight: bold; }")
        self._btn_live.toggled.connect(self._on_live_toggle)
        toolbar.addWidget(self._btn_live)
        self._add_button(toolbar, "Freeze", self._freeze,
                         "Stop following live data; pan and zoom to review")
        self._add_button(toolbar, "Previous", lambda: self.shift_window(-1), "Review the previous time window")
        self._add_button(toolbar, "Next", lambda: self.shift_window(1), "Review the next time window")
        self._add_button(toolbar, "Zoom In", lambda: self._zoom(0.5))
        self._add_button(toolbar, "Zoom Out", lambda: self._zoom(2.0))
        self._add_button(toolbar, "Fit", self.fit_data)

        self._btn_compare = QPushButton("Compare %")
        self._btn_compare.setCheckable(True)
        self._btn_compare.setToolTip(
            "Normalize every pen to its configured engineering range")
        self._btn_compare.toggled.connect(self.set_normalized)
        toolbar.addWidget(self._btn_compare)
        self._ab_button = QPushButton("A/B cursors")
        self._ab_button.setCheckable(True)
        self._ab_button.toggled.connect(self.set_ab_visible)
        toolbar.addWidget(self._ab_button)
        toolbar.addSeparator()
        self._png_button = self._add_button(toolbar, "PNG", self._export_png)
        self._csv_button = self._add_button(toolbar, "CSV", self._export_csv)
        self._more_button = QPushButton("More…")
        self._more_button.clicked.connect(self._compact_menu)
        toolbar.addWidget(self._more_button)
        self._more_button.hide()
        root.addWidget(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._time_axis = HistoryTimeAxis()
        from .history_navigation import HistoryViewBox
        self._plot_widget = pg.PlotWidget(title=self._title, axisItems={"bottom": self._time_axis},
                                          viewBox=HistoryViewBox())
        self._plot_widget.setToolTip(
            "Left-drag: zoom rectangle · Middle-drag: pan · Wheel: zoom at pointer\n"
            "Double-click: fit data · Right-click: historian commands · Drag A/B lines: measure")
        self._plot_widget.setLabel("bottom", "Elapsed time", units="min")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.24)
        self._plot_widget.setMinimumHeight(220)
        self._plot = self._plot_widget.getPlotItem()
        # pyqtgraph creates this control menu without a QWidget owner. Its
        # controls/signals otherwise retain closed charts (and their services).
        # Preserve popup flags: QWidget's one-argument setParent turns this
        # into a child panel, painting "Transforms" over the time selector.
        self._plot.ctrlMenu.setParent(self, self._plot.ctrlMenu.windowFlags())
        self._plot.ctrlMenu.hide()
        self._plot.getAxis("bottom").enableAutoSIPrefix(False)
        self._plot.setLabel("left", "Process value")
        self._plot.vb.setMouseMode(pg.ViewBox.PanMode)
        self._plot.vb.sigRangeChangedManually.connect(self._manual_range_changed)

        self._view_right = HistoryViewBox()
        for view in (self._plot.vb, self._view_right):
            view.navigation.connect(lambda kind, data: self.run_context_action(
                lambda _: self._navigate(kind, data)))
        self._plot.scene().addItem(self._view_right)
        # PlotItem already owns this grid cell. A second AxisItem overlaps its
        # reserved axis and clips the output scale beside a visible legend.
        self._ax_right = self._plot.getAxis("right")
        self._ax_right.linkToView(self._view_right)
        self._view_right.setXLink(self._plot)
        self._ax_right.hide()
        self._plot.setMenuEnabled(False)
        self._view_right.setMenuEnabled(False)
        self._plot_widget.scene().contextMenu = []
        for widget in (self._plot_widget, self._plot_widget.viewport()):
            widget.installEventFilter(self)

        def update_views() -> None:
            self._view_right.setGeometry(self._plot.vb.sceneBoundingRect())
            self._view_right.linkedViewChanged(
                self._plot.vb, self._view_right.XAxis)

        self._plot.vb.sigResized.connect(update_views)
        update_views()

        crosshair_pen = pg.mkPen(DV_ALARM_BAR1, width=1, style=Qt.DashLine)
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=crosshair_pen)
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=crosshair_pen)
        self._plot_widget.addItem(self._vline, ignoreBounds=True)
        self._plot_widget.addItem(self._hline, ignoreBounds=True)
        self._cursor_label = pg.TextItem(
            "", anchor=(0, 1), color=DV_ALARM_BAR1, fill=(24, 31, 42, 210))
        self._plot_widget.addItem(self._cursor_label, ignoreBounds=True)
        self._mouse_proxy = pg.SignalProxy(self._plot_widget.scene().sigMouseMoved,
                                          rateLimit=30, slot=self._on_proxy_mouse_move,
                                          threadSafe=False)
        # Scene mouse events already run on the GUI thread. Parent both the
        # proxy and its timer so closed charts cannot survive via a Qt slot.
        self._mouse_proxy.setParent(self)
        self._mouse_proxy.timer.setParent(self._mouse_proxy)
        self._plot_widget.scene().sigMouseClicked.connect(self._on_mouse_click)
        self._a_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#FFFFFF", width=1.5), label="A")
        self._b_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#FFD54F", width=1.5), label="B")
        for line in (self._a_line, self._b_line):
            self._plot_widget.addItem(line, ignoreBounds=True)
            line.hide()
            line.sigPositionChanged.connect(self._ab_moved)
        splitter.addWidget(self._plot_widget)

        legend_scroll = QScrollArea()
        self._legend_scroll = legend_scroll
        legend_scroll.setWidgetResizable(True)
        legend_scroll.setMinimumWidth(210)
        legend_scroll.setMaximumWidth(280)
        legend_scroll.setStyleSheet(f"background: {BG_PANEL};")
        self._legend_widget = QWidget()
        self._legend_layout = QVBoxLayout(self._legend_widget)
        self._legend_layout.setContentsMargins(6, 6, 6, 6)
        self._legend_layout.setSpacing(4)
        self._legend_layout.setAlignment(Qt.AlignTop)
        self._legend_layout.addWidget(QLabel("<b>Trend Pens</b>"))
        legend_scroll.setWidget(self._legend_widget)
        splitter.addWidget(legend_scroll)
        splitter.setSizes([760, 230])
        root.addWidget(splitter, 1)
        self._ab_label = QLabel("Drag A and B to measure a response interval")
        self._ab_label.setWordWrap(True)
        self._ab_label.hide()
        root.addWidget(self._ab_label)

    @staticmethod
    def _add_button(toolbar, text: str, callback,
                    tooltip: str = "") -> QPushButton:
        button = QPushButton(text)
        from azeo_control_trainer.core.presentation.studio_icons import studio_icon
        names = {"Freeze": "pause", "Previous": "undo", "Next": "redo", "Zoom In": "zoom_in",
                 "Zoom Out": "zoom_out", "Fit": "zoom_fit", "PNG": "print", "CSV": "datalog"}
        if text in names:
            button.setProperty("operator_icon", names[text])
            button.setIcon(studio_icon(names[text], 16))
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(callback)
        toolbar.addWidget(button)
        return button

    def set_compact_layout(self, compact):
        self._legend_scroll.setVisible(self._legend_enabled and not compact)
        self._plot_widget.setMinimumHeight(180 if compact else 220)
        for button in self.toolbar.findChildren(QPushButton):
            if button in (self._btn_live, self._more_button):
                continue
            button.setVisible(not compact and (self._legend_enabled or button not in (self._png_button, self._csv_button)))
            if self._recorded_mode and button.text() == "Freeze":
                button.hide()
        self._more_button.setVisible(compact)
        self._btn_live.setVisible(not self._recorded_mode)

    def set_recorded_mode(self):
        self._recorded_mode = True
        self.set_live_mode(False)
        self._btn_live.hide()
        for button in self.toolbar.findChildren(QPushButton):
            if button.text() == "Freeze":
                button.hide()

    def _compact_menu(self):
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        menu = QMenu(self)
        for button in self.toolbar.findChildren(QPushButton):
            if button in (self._btn_live, self._more_button):
                continue
            if not self._legend_enabled and button in (self._png_button, self._csv_button):
                continue
            if self._recorded_mode and button.text() == "Freeze":
                continue
            action = menu.addAction(button.text())
            if button.isCheckable():
                action.setCheckable(True)
                action.setChecked(button.isChecked())
            action.triggered.connect(button.click)
        try:
            menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))
        finally:
            menu.deleteLater()

    def set_embedded_controls(self, embedded=True):
        self._legend_enabled = not embedded
        self._legend_scroll.setVisible(not embedded)
        self._png_button.setVisible(not embedded)
        self._csv_button.setVisible(not embedded)

    def set_clock_axis(self, origin=None):
        self._time_axis.origin = origin
        self._time_axis.picture = None
        self._time_axis.update()
        self._plot.setLabel("bottom", "Local time" if origin is not None else "Elapsed time",
                            units=None if origin is not None else "min")

    # ------------------------------------------------------------- pens
    def add_pen(self, pen: TrendPen, use_right_axis: bool = False) -> bool:
        """Register a unique pen, enforcing the operator chart limit."""
        if pen.tag in self._pens:
            return True
        if len(self._pens) >= MAX_PENS:
            log.warning("Trend rejected pen %s: the chart already has %d pens",
                        pen.tag, MAX_PENS)
            return False

        pen.axis = "right" if use_right_axis else pen.axis
        curve_pen = pg.mkPen(color=pen.color, width=pen.line_width,
                             style=Qt.DashLine if pen.line_style == "dash" else Qt.SolidLine)
        if pen.axis == "right" and not self._normalized_mode:
            curve = pg.PlotDataItem(pen=curve_pen, connect="finite")
            self._view_right.addItem(curve)
            self._ax_right.show()
        else:
            curve = self._plot_widget.plot([], [], pen=curve_pen,
                                           connect="finite")
        pen._curve = curve
        curve.setClipToView(True)
        curve.setDownsampling(auto=True, method="peak")
        self._pens[pen.tag] = pen
        self._data_cache[pen.tag] = (np.array([]), np.array([]))
        self._create_legend_row(pen)
        self._apply_axis_ranges()
        return True

    def _create_legend_row(self, pen: TrendPen) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        checkbox = QCheckBox()
        checkbox.setChecked(pen.visible)
        checkbox.setToolTip("Show or hide this pen")
        checkbox.setStyleSheet(
            f"QCheckBox::indicator:checked {{ background: {pen.color};"
            " border: 1px solid #66778A; }")
        checkbox.toggled.connect(
            lambda checked, tag=pen.tag: self.set_pen_visible(tag, checked))
        self._pen_checkboxes[pen.tag] = checkbox
        line = QLabel("---" if pen.line_style == "dash" else "—")
        line.setFixedWidth(24)
        line.setStyleSheet(
            f"color: {pen.color}; font-weight: bold; font-size: 10pt;")
        label = QLabel(pen.label)
        label.setToolTip(pen.tag)
        value = QLabel("----")
        value.setMinimumWidth(72)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value.setStyleSheet(
            f"color: {pen.color}; background: {BG_TREND};"
            " font-family: Consolas; padding: 1px 4px;")
        self._pen_value_labels[pen.tag] = value
        row.addWidget(checkbox)
        row.addWidget(line)
        row.addWidget(label, 1)
        row.addWidget(value)
        container = QWidget()
        container.setLayout(row)
        self._legend_layout.addWidget(container)
        self._legend_rows[pen.tag] = container
        self._theme_legend(pen)

    def remove_pen(self, tag: str) -> bool:
        pen = self._pens.pop(tag, None)
        if pen is None:
            return False
        if pen._curve is not None:
            if pen.axis == "right" and not self._normalized_mode:
                self._view_right.removeItem(pen._curve)
            else:
                self._plot_widget.removeItem(pen._curve)
        self._data_cache.pop(tag, None)
        self._data_versions.pop(tag, None)
        self._qualities.pop(tag, None)
        self._pen_checkboxes.pop(tag, None)
        self._pen_value_labels.pop(tag, None)
        row = self._legend_rows.pop(tag, None)
        if row is not None:
            self._legend_layout.removeWidget(row)
            row.deleteLater()
        self._ax_right.setVisible(any(
            one.axis == "right" for one in self._pens.values())
            and not self._normalized_mode)
        self._apply_axis_ranges()
        return True

    def remove_all_pens(self) -> None:
        for tag in tuple(self._pens):
            self.remove_pen(tag)

    def set_pen_visible(self, tag: str, visible: bool) -> None:
        pen = self._pens.get(tag)
        if pen is None:
            return
        changed = pen.visible != bool(visible)
        pen.visible = bool(visible)
        if pen._curve is not None:
            pen._curve.setVisible(pen.visible)
        checkbox = self._pen_checkboxes.get(tag)
        if checkbox is not None and checkbox.isChecked() != pen.visible:
            checkbox.blockSignals(True)
            checkbox.setChecked(pen.visible)
            checkbox.blockSignals(False)
        if changed:
            self.pen_changed.emit(tag)

    def update_pen_style(self, path, **changes):
        pen = self._pens[path]
        for name in ("color", "line_style", "line_width"):
            if name in changes:
                setattr(pen, name, changes[name])
        pen._curve.setPen(pg.mkPen(pen.color, width=pen.line_width,
                                  style=Qt.DashLine if pen.line_style == "dash" else Qt.SolidLine))
        old = self._legend_rows[path]
        index = self._legend_layout.indexOf(old)
        self._legend_layout.removeWidget(old)
        old.hide()
        old.deleteLater()
        self._create_legend_row(pen)
        new = self._legend_rows[path]
        self._legend_layout.removeWidget(new)
        self._legend_layout.insertWidget(index, new)
        self.update_data(path, *self._data_cache[path])
        self.pen_changed.emit(path)

    # ------------------------------------------------------- historian menu
    def eventFilter(self, watched, event):  # noqa: N802
        if hasattr(self, "_plot_widget") and watched in (self._plot_widget, self._plot_widget.viewport()):
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                for view in (self._plot.vb, self._view_right):
                    view.cancel_gesture()
                return True
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                # GraphicsScene synthesizes its double-click on release, while
                # native Qt sends the double-click before that release.
                position = self._plot_widget.mapToScene(event.position().toPoint())
                if self._plot.vb.sceneBoundingRect().contains(position):
                    self.run_context_action(lambda _: self.fit_data())
                    return True
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and event.button() == Qt.RightButton:
                # ViewBox otherwise consumes this gesture for its own menu and
                # right-drag scaling, even when the PlotItem menu is disabled.
                if event.type() == QEvent.MouseButtonRelease:
                    viewport = self._plot_widget.viewport()
                    position = viewport.mapFrom(watched, event.position().toPoint())
                    if viewport.rect().contains(position):
                        self.run_context_action(lambda _: self.open_context_menu(position))
                return True
            if event.type() == QEvent.MouseMove and event.buttons() & Qt.RightButton:
                return True
            if event.type() == QEvent.ContextMenu:
                # Windows may send a context event after the handled release.
                # Keep the first popup and its captured time instead of reopening.
                if self._context_menu is not None and self._context_menu.isVisible():
                    event.accept()
                    return True
                viewport = self._plot_widget.viewport()
                position = (viewport.rect().center() if event.reason() == QContextMenuEvent.Keyboard
                            else viewport.mapFrom(watched, event.pos()))
                self.run_context_action(lambda _: self.open_context_menu(position))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def run_context_action(self, callback, checked=False):
        try:
            callback(checked)
        except Exception as error:  # noqa: BLE001 - a menu action must not escape Qt's event loop
            log.exception("Historian menu action failed")
            self.interaction_error.emit(str(error))

    def build_context_menu(self, time_min, selected_pen=None):
        from .history_menu import build_menu
        return build_menu(self, float(time_min), selected_pen)

    def open_context_menu(self, position):
        scene_position = self._plot_widget.mapToScene(position)
        time_min = float(self._plot.vb.mapSceneToView(scene_position).x())
        selected = None
        distance = 12.0
        for path, value in self._cursor_snapshot(time_min).items():
            if not math.isfinite(value):
                continue
            pen = self._pens[path]
            axis = self._view_right if pen.axis == "right" and not self._normalized_mode else self._plot.vb
            shown = float(self._display_values(pen, np.asarray([value]))[0])
            from PySide6.QtCore import QPointF
            point = axis.mapViewToScene(QPointF(time_min, shown))
            delta = abs(point.y() - scene_position.y())
            if delta < distance:
                selected, distance = path, delta
        if self._context_menu is not None:
            self._context_menu.close()
            self._context_menu.deleteLater()
        self._context_menu = self.build_context_menu(time_min, selected)
        self._context_menu.popup(self._plot_widget.viewport().mapToGlobal(position))

    def displayed_span(self):
        left, right = self._plot.vb.viewRange()[0]
        return max(1 / 60, right - left)

    def _remember_view(self):
        bounds = (self._plot.vb.viewRange(), self._view_right.viewRange()[1])
        if not self._navigation_history or bounds != self._navigation_history[-1]:
            self._navigation_history.append(bounds)
            del self._navigation_history[:-50]

    def zoom_back(self):
        if not self._navigation_history:
            return
        primary, secondary = self._navigation_history.pop()
        self.set_live_mode(False)
        self._plot.vb.setRange(xRange=primary[0], yRange=primary[1], padding=0)
        self._view_right.setYRange(*secondary, padding=0)
        self._manual_range_changed()

    def _navigate(self, kind, data):
        if kind == "error":
            self.interaction_error.emit(data)
        elif kind == "begin":
            self._remember_view()
            self.set_live_mode(False)
            self.navigation_started.emit()
        elif kind == "rectangle":
            # Map both axes before changing either transform. The secondary
            # view overlays the primary, so its default pan used to win hits.
            primary = self._plot.vb.mapSceneToView(data.topLeft())
            opposite = self._plot.vb.mapSceneToView(data.bottomRight())
            secondary = self._view_right.mapSceneToView(data.topLeft())
            secondary_end = self._view_right.mapSceneToView(data.bottomRight())
            self._plot.vb.setRange(xRange=sorted((primary.x(), opposite.x())),
                                   yRange=sorted((primary.y(), opposite.y())), padding=0)
            self._view_right.setYRange(*sorted((secondary.y(), secondary_end.y())), padding=0)
            self._manual_range_changed()
        elif kind == "pan":
            delta, axis = data
            moves = []
            for view in (self._plot.vb, self._view_right):
                centre = view.sceneBoundingRect().center()
                moves.append(view.mapSceneToView(centre) - view.mapSceneToView(centre + delta))
            self._plot.vb.translateBy(x=moves[0].x() if axis != 1 else None,
                                      y=moves[0].y() if axis != 0 else None)
            if axis != 0:
                self._view_right.translateBy(y=moves[1].y())
        elif kind == "finish":
            self._manual_range_changed()
        elif kind == "wheel":
            self._remember_view()
            self.set_live_mode(False)
            self.navigation_started.emit()
            scene, factor, axis = data
            centres = [view.mapSceneToView(scene) for view in (self._plot.vb, self._view_right)]
            self._plot.vb.scaleBy(x=factor if axis != 1 else None,
                                  y=factor if axis != 0 else None, center=centres[0])
            if axis != 0:
                self._view_right.scaleBy(y=factor, center=centres[1])
            self._manual_range_changed()
        elif kind == "fit":
            self.fit_data()

    def review_at(self, time_min, minutes=None):
        self._remember_view()
        minutes = max(1 / 60, float(minutes if minutes is not None else self.displayed_span()))
        self.set_live_mode(False)
        self.set_time_window(minutes)
        self.set_review_range(time_min - minutes / 2, time_min + minutes / 2)
        self.review_range_changed.emit(time_min - minutes / 2, time_min + minutes / 2)

    def place_cursor(self, name, time_min):
        self.set_live_mode(False)
        self._ab_button.setChecked(True)
        (self._a_line if name == "A" else self._b_line).setValue(time_min)

    def review_between_cursors(self):
        start, end = sorted((self._a_line.value(), self._b_line.value()))
        if end > start:
            self._remember_view()
            self.set_review_range(start, end)
            self.review_range_changed.emit(start, end)

    def show_only(self, path):
        for tag in self._pens:
            self.set_pen_visible(tag, path is None or path == tag)

    def auto_scale_values(self):
        self._plot.enableAutoRange(axis="y")
        self._view_right.enableAutoRange(axis="y")

    def configured_scales(self):
        if self._normalized_mode:
            self._plot.setYRange(0, 100, padding=0)
            return
        for axis, view in (("left", self._plot.vb), ("right", self._view_right)):
            pens = [pen for pen in self._pens.values() if pen.axis == axis and pen.visible]
            if pens:
                lo, hi = min(pen.y_lo for pen in pens), max(pen.y_hi for pen in pens)
                if hi > lo:
                    view.setYRange(lo, hi, padding=0)

    def show_grid(self, visible):
        self._grid_visible = bool(visible)
        self._plot_widget.showGrid(x=visible, y=visible, alpha=.24)

    def show_event_markers(self, visible):
        self._events_visible = bool(visible)
        for item in self._event_items:
            item.setVisible(visible)

    # ------------------------------------------------------------- data
    def update_data(self, tag: str, times: np.ndarray,
                    values: np.ndarray) -> None:
        pen = self._pens.get(tag)
        if pen is None:
            return
        times = np.asarray(times, dtype=float)
        values = np.asarray(values, dtype=float)
        self._data_cache[tag] = (times, values)
        if values.size:
            label = self._pen_value_labels.get(tag)
            if label is not None:
                label.setText(
                    f"{values[-1]:,.2f} {pen.unit}" if math.isfinite(values[-1]) else "BAD")
        if pen._curve is None:
            return
        if not times.size:
            pen._curve.setData([], [])
            return
        if self._live_mode and self._time_window_min < 1e8:
            mask = times >= times[-1] - self._time_window_min
            shown_times, shown_values = times[mask], values[mask]
        else:
            shown_times, shown_values = times, values
        pen._curve.setData(
            shown_times, self._display_values(pen, shown_values),
            connect="finite")

    def update_all(self, historian) -> None:
        self._historian = historian
        for tag in tuple(self._pens):
            if tag in historian.TAGS:
                point = historian.TAGS[tag]
                interval = (None, None)
                budget = max(256, min(12000, int(self._plot.vb.width()) * 2))
                if hasattr(historian, "get_plot_series"):
                    bounds = historian.series_bounds(tag)
                    if self._live_mode:
                        interval = (bounds[1] - self._time_window_min, None) if bounds and self._time_window_min < 1e8 else (None, None)
                    else:
                        left, right = self.visible_time_range()
                        margin = (right - left) * .05
                        interval = (left - margin, right + margin)
                version = (id(historian), id(point), getattr(point, "version", None),
                           getattr(point, "unit", None), getattr(point, "metadata_conflict", False), interval, budget)
                if version[2] is None or self._data_versions.get(tag) != version:
                    if hasattr(historian, "get_plot_series"):
                        times, values, qualities = historian.get_plot_series(tag, *interval, budget=budget)
                        self.update_data(tag, times, values)
                        self._qualities[tag] = qualities
                    else:
                        self.update_data(tag, *historian.get_series(tag))
                        if hasattr(historian, "get_quality_series"):
                            self._qualities[tag] = historian.get_quality_series(tag)
                    self._data_versions[tag] = version
                if hasattr(historian, "latest"):
                    latest = historian.latest(tag)
                    if latest["quality"] not in {"GOOD", "UNCERTAIN"}:
                        self._pen_value_labels[tag].setText(latest["quality"])
                    elif latest["quality"] == "UNCERTAIN":
                        self._pen_value_labels[tag].setText(f"{latest['value']:,.2f} ? {point.unit}")
        if self._live_mode:
            self._scroll_to_latest()

    def _display_values(self, pen: TrendPen, values: np.ndarray) -> np.ndarray:
        if not self._normalized_mode:
            return values
        span = pen.y_hi - pen.y_lo
        if not math.isfinite(span) or abs(span) < 1e-12:
            return np.full_like(values, np.nan)
        return 100.0 * (values - pen.y_lo) / span

    # ---------------------------------------------------------- controls
    def set_normalized(self, normalized: bool) -> None:
        normalized = bool(normalized)
        if self._normalized_mode == normalized:
            return
        self._navigation_history.clear()
        caches = dict(self._data_cache)
        configs = [
            TrendPen(
                pen.tag, pen.label, pen.unit, pen.color, pen.y_lo, pen.y_hi,
                pen.line_width, pen.visible, pen.axis, line_style=pen.line_style)
            for pen in self._pens.values()
        ]
        self.remove_all_pens()
        self._normalized_mode = normalized
        self._btn_compare.blockSignals(True)
        self._btn_compare.setChecked(normalized)
        self._btn_compare.blockSignals(False)
        for pen in configs:
            self.add_pen(pen, use_right_axis=pen.axis == "right")
            times, values = caches.get(pen.tag, (np.array([]), np.array([])))
            self.update_data(pen.tag, times, values)
            self.set_pen_visible(pen.tag, pen.visible)
        self._apply_axis_ranges()

    def is_normalized(self) -> bool:
        return self._normalized_mode

    def set_time_window(self, minutes: float) -> None:
        self._time_window_min = max(0.1, float(minutes))
        label = next((key for key, value in _WINDOWS.items()
                      if math.isclose(value, self._time_window_min)), None)
        with QSignalBlocker(self._window_combo):
            if self._window_combo.count() > len(_WINDOWS):
                self._window_combo.removeItem(len(_WINDOWS))
            if label is None:
                label = f"{self._time_window_min:g} min"
                self._window_combo.addItem(label, self._time_window_min)
            self._window_combo.setCurrentText(label)
        if self._live_mode:
            self._scroll_to_latest()
        self._refresh_plot_range()
        self.window_changed.emit(self._time_window_min)

    def _refresh_plot_range(self):
        if self._historian is not None:
            self.update_all(self._historian)

    def time_window_min(self) -> float:
        return self._time_window_min

    def set_live_mode(self, live: bool) -> None:
        self._btn_live.setChecked(bool(live))

    def is_live(self) -> bool:
        return self._live_mode

    def follow_latest(self) -> None:
        """Refresh the visible window after supplying a batch of recorded data."""
        if self._live_mode:
            self._scroll_to_latest()
            self._view_right.enableAutoRange(axis="y")

    def visible_time_range(self) -> tuple[float | None, float | None]:
        if self._live_mode:
            latest = max(
                (float(times[-1]) for times, _ in self._data_cache.values()
                 if times.size), default=None)
            if latest is None:
                return None, None
            start = None if self._time_window_min >= 1e8 else (
                latest - self._time_window_min)
            return start, latest
        x_range = self._plot.vb.viewRange()[0]
        return float(x_range[0]), float(x_range[1])

    def _apply_axis_ranges(self) -> None:
        if self._normalized_mode:
            self._plot.setLabel("left", "Configured span", units="%")
            self._plot.setYRange(0.0, 100.0, padding=0.04)
            self._ax_right.hide()
            return
        self._plot.setLabel("left", "Process value")
        left = [p for p in self._pens.values() if p.axis != "right"]
        right = [p for p in self._pens.values() if p.axis == "right"]
        self._plot.setLabel("left", " / ".join(sorted({p.unit for p in left if p.unit})) or "Process value")
        if len(left) == 1 and left[0].y_hi > left[0].y_lo:
            self._plot.setYRange(left[0].y_lo, left[0].y_hi, padding=0.04)
        else:
            self._plot.enableAutoRange(axis="y")
        if right:
            self._ax_right.show()
            lo = min(p.y_lo for p in right)
            hi = max(p.y_hi for p in right)
            if hi > lo:
                self._view_right.setRange(yRange=(lo, hi), padding=0.04)
            units = sorted({p.unit for p in right if p.unit})
            self._ax_right.setLabel(" / ".join(units) or "Secondary scale")
        else:
            self._ax_right.hide()

    def _scroll_to_latest(self) -> None:
        latest = max(
            (float(times[-1]) for times, _ in self._data_cache.values()
             if times.size), default=None)
        if latest is None:
            return
        if self._time_window_min >= 1e8:
            starts = [float(times[0]) for times, _ in self._data_cache.values()
                      if times.size]
            start = min(starts, default=0.0)
        else:
            start = latest - self._time_window_min
        self._plot.setXRange(start, latest, padding=0.01)

    def _on_window_change(self, text: str) -> None:
        minutes = _WINDOWS.get(text, self._window_combo.currentData())
        if minutes is not None:
            self.set_time_window(minutes)

    def _on_live_toggle(self, live: bool) -> None:
        self._live_mode = bool(live)
        self._data_versions.clear()
        for path, (times, values) in list(self._data_cache.items()):
            self.update_data(path, times, values)
        self._refresh_plot_range()
        self._btn_live.setText("Live" if live else "Return to Live")
        if live:
            self._scroll_to_latest()
        self.live_changed.emit(live)

    def _freeze(self) -> None:
        self._btn_live.setChecked(False)

    def _zoom(self, factor: float) -> None:
        self._remember_view()
        if self._live_mode:
            self.set_time_window(min(1e9, max(0.1, self._time_window_min * factor)))
        else:
            left, right = self._plot.vb.viewRange()[0]
            centre, half = (left + right) / 2, (right - left) * factor / 2
            self._plot.setXRange(centre - half, centre + half, padding=0)
            self._refresh_plot_range()
            self.review_range_changed.emit(centre - half, centre + half)

    def shift_window(self, direction):
        self._remember_view()
        left, right = self._plot.vb.viewRange()[0]
        self.set_live_mode(False)
        shift = (right - left) * direction
        self._plot.setXRange(left + shift, right + shift, padding=0)
        self._refresh_plot_range()
        self.review_range_changed.emit(left + shift, right + shift)

    def _manual_range_changed(self, *_):
        self.set_live_mode(False)
        left, right = self._plot.vb.viewRange()[0]
        self._refresh_plot_range()
        self.review_range_changed.emit(left, right)

    def set_review_range(self, start, end):
        self.set_live_mode(False)
        self._plot.setXRange(start, end, padding=0)
        self._refresh_plot_range()

    def fit_data(self) -> None:
        self._remember_view()
        self._btn_live.setChecked(False)
        intervals = [(float(times[0]), float(times[-1])) for path, (times, _) in self._data_cache.items()
                     if times.size and self._pens[path].visible]
        if hasattr(self._historian, "series_bounds"):
            intervals = [bounds for path, pen in self._pens.items() if pen.visible
                         and (bounds := self._historian.series_bounds(path)) is not None]
        if intervals:
            start, end = min(row[0] for row in intervals), max(row[1] for row in intervals)
            # A clipped curve only exposes its current window to auto-ranging.
            # Use recorded time bounds, and never auto-range the linked X view.
            self._plot.setXRange(start, max(end, start + 1 / 60), padding=.02)
            self._refresh_plot_range()
        self._plot.enableAutoRange(axis="y")
        self._view_right.enableAutoRange(axis="y")
        if intervals:
            self.review_range_changed.emit(*self._plot.vb.viewRange()[0])

    # ----------------------------------------------------------- cursor
    def focus_time(self, time_min: float) -> None:
        """Locate an event on the same time axis as the recorded traces."""
        time_min = float(time_min)
        self.set_live_mode(False)
        self._vline.setPos(time_min)
        left, right = self._plot.vb.viewRange()[0]
        if not left <= time_min <= right:
            half = max(0.05, (right - left) / 2)
            self._plot.setXRange(time_min - half, time_min + half, padding=0)
        self.cursor_changed.emit(time_min, self._cursor_snapshot(time_min))

    def _cursor_snapshot(self, time_min: float) -> dict[str, float]:
        if self._historian is not None and hasattr(self._historian, "nearest_values"):
            paths = [path for path, pen in self._pens.items() if pen.visible]
            return {path: record[0] for path, record in self._historian.nearest_values(paths, time_min).items()}
        values: dict[str, float] = {}
        for tag, pen in self._pens.items():
            if not pen.visible:
                continue
            times, samples = self._data_cache.get(
                tag, (np.array([]), np.array([])))
            if not times.size:
                continue
            if time_min < times[0] or time_min > times[-1]:
                continue
            index = int(np.searchsorted(times, time_min))
            candidates = [i for i in (index - 1, index) if 0 <= i < len(times)]
            nearest = min(candidates,
                          key=lambda i: abs(float(times[i]) - time_min))
            values[tag] = float(samples[nearest])
        return values

    def _on_proxy_mouse_move(self, args) -> None:
        self._on_mouse_move(args[0])

    def _on_mouse_move(self, pos) -> None:
        if not self._plot_widget.sceneBoundingRect().contains(pos):
            return
        point = self._plot.vb.mapSceneToView(pos)
        time_min = float(point.x())
        self._vline.setPos(time_min)
        self._hline.setPos(float(point.y()))
        self._cursor_label.setPos(time_min, float(point.y()))
        snapshot = self._cursor_snapshot(time_min)
        lines = [f"t = {time_min:.3f} min"]
        for tag, value in snapshot.items():
            pen = self._pens[tag]
            shown = "BAD" if not math.isfinite(value) else f"{value:.4g} {pen.unit}"
            lines.append(f"{pen.label}: {shown}")
        self._cursor_label.setText("\n".join(lines))
        self.cursor_changed.emit(time_min, snapshot)

    def set_ab_visible(self, visible):
        left, right = self._plot.vb.viewRange()[0]
        self._a_line.setValue(left + (right - left) * .25)
        self._b_line.setValue(left + (right - left) * .75)
        self._a_line.setVisible(visible)
        self._b_line.setVisible(visible)
        self._ab_label.setVisible(visible)
        self._ab_moved()

    def _ab_moved(self):
        a, b = self._a_line.value(), self._b_line.value()
        dt = (b - a) * 60
        av, bv = self._cursor_snapshot(a), self._cursor_snapshot(b)
        aq = self._historian.nearest_values(list(self._pens), a) if hasattr(self._historian, "nearest_values") else {}
        bq = self._historian.nearest_values(list(self._pens), b) if hasattr(self._historian, "nearest_values") else {}
        details = [f"A → B: {dt:+.2f} s"]
        for path, pen in self._pens.items():
            if path in av and path in bv and all(math.isfinite(v) for v in (av[path], bv[path])):
                if aq and (aq.get(path, (None, "BAD"))[1] != "GOOD" or bq.get(path, (None, "BAD"))[1] != "GOOD"):
                    continue
                delta = bv[path] - av[path]
                rate = f" · {delta / dt:+.3f} {pen.unit}/s" if abs(dt) > 1e-9 else ""
                details.append(f"{pen.label}: Δ {delta:+.3f} {pen.unit}{rate}")
        self._ab_label.setText("   |   ".join(details))
        self.ab_changed.emit(a, b)

    def set_events(self, rows):
        for item in self._event_items:
            self._plot_widget.removeItem(item)
        self._event_items.clear()
        palette = {"alarm": "#F4A261", "operator": "#8EC5FF", "mode": "#CDB4DB",
                   "training": "#95D5B2", "clock": "#CBD5E1", "note": "#FFD54F"}
        for row in list(rows)[-120:]:
            line = pg.InfiniteLine(pos=row["time"] / 60, angle=90, movable=False,
                                   pen=pg.mkPen(palette.get(row["category"], "#CBD5E1"), width=1, style=Qt.DotLine))
            line.setToolTip(f"{row['action']}\n{row.get('target', '')}\n{row.get('detail', '')}")
            line.sigClicked.connect(lambda _line, event, entry=row: self.event_selected.emit(entry)
                                    if event.button() == Qt.LeftButton else None)
            self._plot_widget.addItem(line, ignoreBounds=True)
            line.setVisible(self._events_visible)
            self._event_items.append(line)

    def _on_mouse_click(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        point = self._plot.vb.mapSceneToView(event.scenePos())
        self._btn_live.setChecked(False)
        self.time_selected.emit(float(point.x()))

    # ------------------------------------------------------------ export
    def _export_png(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Trend PNG", "trend.png", "PNG Files (*.png)")
        if not path:
            return
        from pyqtgraph.exporters import ImageExporter
        ImageExporter(self._plot).export(path)
        log.info("Trend exported to PNG: %s", path)

    def _export_csv(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Trend CSV", "trend.csv", "CSV Files (*.csv)")
        if not path:
            return
        timeline = sorted({float(t) for times, _ in self._data_cache.values()
                           for t in times})
        indexed = {
            tag: {float(t): float(v) for t, v in zip(times, values)}
            for tag, (times, values) in self._data_cache.items()
        }
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Time_min", *self._pens])
                for timestamp in timeline:
                    row = [f"{timestamp:.6f}"]
                    for tag in self._pens:
                        value = indexed.get(tag, {}).get(timestamp, math.nan)
                        row.append(f"{value:.8g}" if math.isfinite(value) else "")
                    writer.writerow(row)
            log.info("Trend exported to CSV: %s", path)
        except OSError:
            log.exception("CSV export failed: %s", path)
