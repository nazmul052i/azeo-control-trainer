"""Operator trend commands over the shared chart and its host services."""
from __future__ import annotations

import csv
from datetime import datetime
import io
import math

from PySide6.QtGui import QAction, QActionGroup, QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QLabel, QMenu, QVBoxLayout, QWidgetAction,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.studio_icons import studio_icon


def _action(chart, menu, key, label, callback, *, checked=None, enabled=True, icon=None):
    item = QAction(label, menu)
    if icon:
        item.setIcon(studio_icon(icon, 16))
    menu.addAction(item)
    item.setObjectName(key)
    item.setEnabled(enabled)
    if checked is not None:
        item.setCheckable(True)
        item.setChecked(checked)
    item.triggered.connect(lambda value=False: chart.run_context_action(callback, value))
    menu._history_actions = getattr(menu, "_history_actions", []) + [item]
    return item


def _submenu(menu, label, icon=None):
    child = QMenu(label, menu)
    if icon:
        child.setIcon(studio_icon(icon, 16))
    menu.addMenu(child)
    # Retain wrappers as well as C++ ownership. Rediscovering a submenu through
    # QAction.menu() can otherwise release its actions during menu traversal.
    menu._history_menus = getattr(menu, "_history_menus", []) + [child]
    return child


def _service(chart, menu, key, label, time_min, *, icon=None, enabled=True):
    if key in chart.context_services:
        _action(chart, menu, key, label,
                lambda _: chart.context_services[key](time_min), enabled=enabled, icon=icon)


def build_menu(chart, time_min, selected_pen=None):
    """Construct afresh so availability and checks describe current chart state."""
    from .historian_trend import _WINDOWS
    menu = QMenu(chart)
    menu.setObjectName("historian_context_menu")
    menu.setStyleSheet(f"""
        QMenu {{ background: {UI.pane}; color: {UI.text}; border: 1px solid {UI.border};
                 padding: 6px 0; min-width: 280px; }}
        QMenu::item {{ padding: 6px 28px 6px 30px; }}
        QMenu::item:selected {{ background: {UI.selection}; color: {UI.blue}; }}
        QMenu::item:checked {{ background: {UI.selection}; color: {UI.blue}; }}
        QMenu::item:disabled {{ color: {UI.disabled}; }}
        QMenu::separator {{ height: 1px; background: {UI.border_light}; margin: 5px 10px; }}
    """)
    origin = chart._time_axis.origin
    if origin is None:
        origin = getattr(chart._historian, "origin", None)
    stamp = (datetime.fromtimestamp(origin + time_min * 60).astimezone().strftime("%d %b %Y  %H:%M:%S")
             if origin is not None else f"Elapsed time  {time_min:.3f} min")
    heading = QWidgetAction(menu)
    heading.setObjectName("context_time")
    label = QLabel(stamp, menu)
    label.setStyleSheet(f"padding: 6px 12px; color: {UI.blue}; font-weight: 600;")
    heading.setDefaultWidget(label)
    heading.setEnabled(False)
    menu.addAction(heading)
    menu._history_heading = (heading, label)
    menu.addSeparator()
    if not chart._recorded_mode:
        _action(chart, menu, "live", "Return to live", lambda _: chart.set_live_mode(True),
                checked=chart.is_live(), icon="run_scan")
        _action(chart, menu, "freeze", "Freeze for review", lambda _: chart.set_live_mode(False),
                checked=not chart.is_live(), icon="pause")
    _action(chart, menu, "center", "Center this time", lambda _: chart.review_at(time_min), icon="search")
    navigation = _submenu(menu, "Time range", "history")
    _action(chart, navigation, "previous", "Previous interval", lambda _: chart.shift_window(-1), icon="undo")
    _action(chart, navigation, "next", "Next interval", lambda _: chart.shift_window(1), icon="redo")
    navigation.addSeparator()
    for label, duration in _WINDOWS.items():
        if label == "All":
            continue
        _action(chart, navigation, f"window:{label}", label,
                lambda _, minutes=duration: chart.review_at(time_min, minutes))
    navigation.addSeparator()
    _service(chart, navigation, "archive", "Select date / time range…", time_min, icon="history")
    zoom = _submenu(menu, "Zoom", "zoom_fit")
    _action(chart, zoom, "zoom_back", "Zoom back", lambda _: chart.zoom_back(),
            enabled=bool(chart._navigation_history), icon="undo")
    _action(chart, zoom, "zoom_in", "Zoom in here", lambda _: chart.review_at(time_min, chart.displayed_span() / 2), icon="zoom_in")
    _action(chart, zoom, "zoom_out", "Zoom out here", lambda _: chart.review_at(time_min, chart.displayed_span() * 2), icon="zoom_out")
    measured = chart._ab_button.isChecked() and abs(chart._a_line.value() - chart._b_line.value()) > 1e-9
    _action(chart, zoom, "zoom_ab", "Zoom between A and B", lambda _: chart.review_between_cursors(), enabled=measured)
    _action(chart, zoom, "fit", "Fit recorded data", lambda _: chart.fit_data(),
            enabled=any(times.size for times, _ in chart._data_cache.values()), icon="zoom_fit")

    menu.addSeparator()
    cursors = _submenu(menu, "Cursors and measurements", "select_all")
    _action(chart, cursors, "cursor_a", "Set A here", lambda _: chart.place_cursor("A", time_min))
    _action(chart, cursors, "cursor_b", "Set B here", lambda _: chart.place_cursor("B", time_min))
    _action(chart, cursors, "ab_visible", "Show A/B measurements", lambda checked: chart._ab_button.setChecked(checked),
            checked=chart._ab_button.isChecked())
    _action(chart, cursors, "copy_values", "Copy values at this time", lambda _: copy_values(chart, time_min),
            enabled=bool(chart._pens), icon="copy")
    _service(chart, cursors, "statistics", "Show interval statistics", time_min, icon="values")

    if selected_pen in chart._pens:
        selected = chart._pens[selected_pen]
        detail = _submenu(menu, f"Selected pen · {selected.label}", "datalog")
        _pen_menu(chart, detail, selected_pen)
    pens = _submenu(menu, f"Pens ({len(chart._pens)})", "datalog")
    _service(chart, pens, "add_pen", "Add pens…", time_min, icon="new", enabled=len(chart._pens) < 10)
    if chart._pens:
        _action(chart, pens, "show_all", "Show all pens", lambda _: chart.show_only(None))
        pens.addSeparator()
    for path, pen in chart._pens.items():
        child = _submenu(pens, pen.tag)
        child.setToolTipsVisible(True)
        _pen_menu(chart, child, path)

    scales = _submenu(menu, "Value scales", "values")
    group = QActionGroup(scales)
    scales._history_group = group
    group.setExclusive(True)
    for label, value, key in (("Engineering units", False, "engineering"), ("Compare configured spans (%)", True, "normalize")):
        group.addAction(_action(chart, scales, key, label, lambda _, mode=value: chart.set_normalized(mode),
                                checked=chart.is_normalized() == value))
    scales.addSeparator()
    _action(chart, scales, "auto_scale", "Auto scale visible values", lambda _: chart.auto_scale_values())
    _action(chart, scales, "configured_scale", "Use configured ranges", lambda _: chart.configured_scales())
    _action(chart, scales, "axis_limits", "Set axis limits…", lambda _: edit_axis_limits(chart))
    appearance = _submenu(menu, "Chart appearance")
    _action(chart, appearance, "grid", "Show grid", lambda checked: chart.show_grid(checked), checked=chart._grid_visible)
    if chart._event_items or "events" in chart.context_services:
        _action(chart, appearance, "events_visible", "Show event markers", lambda checked: chart.show_event_markers(checked),
                checked=chart._events_visible)
    _service(chart, appearance, "clock", "Switch elapsed / local time", time_min, icon="history")

    menu.addSeparator()
    _service(chart, menu, "bookmark", "Bookmark / note at this time…", time_min, icon="comment")
    _service(chart, menu, "events", "Review events at this time", time_min, icon="history")
    _service(chart, menu, "compare_runs", "Compare process responses…", time_min, icon="compare")
    _service(chart, menu, "save_group", "Save trend group…", time_min, icon="save")
    if "export" in chart.context_services:
        _service(chart, menu, "export", "Export history / report…", time_min, icon="datalog", enabled=bool(chart._pens))
        if measured:
            _service(chart, menu, "export_ab", "Export between A and B…", time_min, icon="datalog")
    else:
        exports = _submenu(menu, "Export", "datalog")
        _action(chart, exports, "png", "Chart image…", lambda _: chart._export_png(), enabled=bool(chart._pens))
        _action(chart, exports, "csv", "Recorded values CSV…", lambda _: chart._export_csv(), enabled=bool(chart._pens))
    return menu


def _pen_menu(chart, menu, path):
    pen = chart._pens[path]
    _action(chart, menu, f"pen_hide:{path}", "Visible", lambda checked: chart.set_pen_visible(path, checked), checked=pen.visible)
    _action(chart, menu, f"pen_solo:{path}", "Show only this pen", lambda _: chart.show_only(path))
    style = _submenu(menu, "Line style")
    group = QActionGroup(style)
    style._history_group = group
    for label, value in (("Solid", "solid"), ("Dashed", "dash")):
        group.addAction(_action(chart, style, f"style:{path}:{value}", label,
                                lambda _, value=value: chart.update_pen_style(path, line_style=value),
                                checked=pen.line_style == value))
    widths = _submenu(menu, "Line width")
    group = QActionGroup(widths)
    widths._history_group = group
    for width in (1.0, 1.5, 2.0, 3.0):
        group.addAction(_action(chart, widths, f"width:{path}:{width}", f"{width:g} px",
                                lambda _, value=width: chart.update_pen_style(path, line_width=value),
                                checked=pen.line_width == width))
    _action(chart, menu, f"color:{path}", "Pen color…", lambda _: choose_color(chart, path), icon="properties")
    _action(chart, menu, f"copy_path:{path}", "Copy point path", lambda _: QApplication.clipboard().setText(path), icon="copy")
    if "remove_pen" in chart.context_services:
        menu.addSeparator()
        _action(chart, menu, f"remove:{path}", "Remove from this chart",
                lambda _: chart.context_services["remove_pen"](path), icon="delete")


def choose_color(chart, path):
    if is_headless():
        return
    colour = QColorDialog.getColor(QColor(chart._pens[path].color), chart, "Trend pen color")
    if colour.isValid():
        chart.update_pen_style(path, color=colour.name())


def copy_values(chart, time_min):
    source = chart._historian
    if hasattr(source, "nearest_values"):
        records = source.nearest_values([path for path, pen in chart._pens.items() if pen.visible], time_min)
    else:
        records = {}
        for path, value in chart._cursor_snapshot(time_min).items():
            times, _ = chart._data_cache[path]
            index = min(range(len(times)), key=lambda i: abs(float(times[i]) - time_min))
            qualities = chart._qualities.get(path, ())
            quality = str(qualities[index]) if index < len(qualities) else "NOT RECORDED"
            records[path] = (value, quality if math.isfinite(value) else "BAD", float(times[index]))
    text = io.StringIO()
    writer = csv.writer(text, delimiter="\t", lineterminator="\n")
    writer.writerow(("Point", "Requested minute", "Sample minute", "Value", "Unit", "Quality"))
    def safe(value):
        return "'" + value if value.startswith(("=", "+", "-", "@")) else value
    for path, pen in chart._pens.items():
        if not pen.visible:
            continue
        value, quality, actual = records.get(path, (math.nan, "NO SAMPLE", None))
        writer.writerow((safe(path), time_min, actual if actual is not None else "",
                         value if math.isfinite(value) else "", safe(pen.unit), quality))
    QApplication.clipboard().setText(text.getvalue())


class AxisLimitsDialog(QDialog):
    def __init__(self, chart):
        super().__init__(chart)
        self.setWindowTitle("Historian value scales")
        root = QVBoxLayout(self)
        note = QLabel("Set chart limits without changing the point's configured engineering range.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.inputs = {}
        form = QFormLayout()
        for name, view in (("Primary", chart._plot.vb), ("Secondary", chart._view_right)):
            if name == "Secondary" and (chart.is_normalized() or not any(p.axis == "right" for p in chart._pens.values())):
                continue
            auto = QCheckBox("Automatic")
            auto.setChecked(bool(view.autoRangeEnabled()[1]))
            bounds = []
            form.addRow(name, auto)
            for label, value in zip(("Minimum", "Maximum"), view.viewRange()[1]):
                editor = QDoubleSpinBox()
                editor.setDecimals(6)
                editor.setRange(-1e15, 1e15)
                editor.setValue(value)
                editor.setEnabled(not auto.isChecked())
                auto.toggled.connect(lambda checked, widget=editor: widget.setEnabled(not checked))
                form.addRow(label, editor)
                bounds.append(editor)
            self.inputs[name] = (auto, bounds)
        root.addLayout(form)
        self.error = QLabel()
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self):
        for automatic, bounds in self.inputs.values():
            if not automatic.isChecked() and bounds[0].value() >= bounds[1].value():
                self.error.setText("Maximum must be greater than minimum.")
                return
        super().accept()


def edit_axis_limits(chart):
    if is_headless():
        return
    dialog = AxisLimitsDialog(chart)
    if dialog.exec() == QDialog.Accepted:
        for name, (automatic, bounds) in dialog.inputs.items():
            view = chart._plot.vb if name == "Primary" else chart._view_right
            if automatic.isChecked():
                view.enableAutoRange(axis="y")
            else:
                view.setYRange(bounds[0].value(), bounds[1].value(), padding=0)
