"""A theme is presentation state, never a display reload or operating action."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from azeo_control_trainer.azeo_operator_station.console import LiveStation
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
from azeo_control_trainer.azeo_operator_station.shell.chrome import ConsoleSettings
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import THEMES


@pytest.fixture(scope="module")
def app():
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def stations(app, tmp_path):
    original_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "preferences"))
    windows = []

    def create(name="one", default="silver"):
        root = tmp_path / name
        store = DisplayStore(root / "pvm")
        if not store.history("Plant"):
            for title in ("Plant", "Unit"):
                document = PvmDisplay(name=title, width=900, height=600, items=[{
                    "id": "panel", "kind": "rect", "x": 20, "y": 20,
                    "w": 100, "h": 100, "fill_role": "SURFACE_PANEL"}])
                store.save_draft(document)
                store.publish(document, by="test")
        station = LiveStation(PvmDeployment(store), lambda: {}, config_root=root,
                              settings=ConsoleSettings(theme=default))
        station._tick.stop()
        station.resize(1100, 800)
        windows.append(station)
        return station

    yield create
    for window in reversed(windows):
        window.close()
        window.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QSettings.setDefaultFormat(original_format)


def test_switch_preserves_visible_and_cached_views_without_lifecycle(stations, app):
    window = stations()
    window.show_display("Plant")
    first = window.view
    first.zoom_by(1.3)
    transform = first.transform()
    scene, engine = first.scene(), first.engine
    events = []
    first.display.events = {"open": [{"action": "open"}], "close": [{"action": "close"}]}
    first._action_handler = lambda *args: events.append(args)
    window.show_display("Unit")
    second = window.view
    events.clear()
    window.choose_theme("dark")
    assert window.view is second, "Theme selection rebuilt the active process view"
    assert first.scene() is scene and first.engine is engine
    assert first.transform() == transform
    assert not first._timer.isActive()
    assert events == [], "Theme selection executed a display lifecycle action"
    assert first.palette_roles is THEMES["dark"]
    assert window.show_display("Plant")
    assert window.view is first
    assert window.view.transform() == transform
    app.processEvents()


def test_theme_is_saved_per_console_and_reset_uses_console_default(stations):
    window = stations(default="silver")
    assert next(row for row in window.theme_choices() if row[2])[0] == "silver"
    assert window.choose_theme("dark")
    window.close()
    reopened = stations(default="silver")
    assert reopened.settings.theme == "dark"
    assert reopened.themes.current == "dark"
    other = stations("two", default="hpgray")
    assert other.settings.theme == "hpgray"
    assert reopened.restore_default_theme()
    reopened.close()
    assert stations().settings.theme == "silver"


def test_theme_rejects_unknown_request_without_changing_state(stations):
    window = stations()
    observed = []
    window.themes.changed.connect(observed.append)
    assert not window.choose_theme("../../dark.qss")
    assert not window.choose_theme("silver")
    assert window.settings.theme == "silver"
    assert observed == []


def test_operator_toolbar_uses_hmi_theme_without_changing_engineering(stations, app):
    window = stations()
    other = stations("two")
    engineering = QWidget()
    engineering.setStyleSheet("QWidget { background: #E3E9EF; color: #004487; }")
    app_palette = app.palette()
    style = engineering.styleSheet()
    try:
        window.show()
        window.choose_theme("dark")
        app.processEvents()
        button = window.menu._controls["search"]
        assert button.palette().color(QPalette.ButtonText).name() == THEMES["dark"][Role.ACTION].lower()
        assert app.palette() == app_palette
        assert engineering.styleSheet() == style
        assert other.settings.theme == "silver"
    finally:
        engineering.close()


def test_faceplate_theme_does_not_poll_or_replace_pending_input(stations, monkeypatch):
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget

    window = stations()
    engine = BindingEngine(LiveGraphSource(lambda: {}))
    faceplate = PvmFaceplateWidget(registry.get("PID", "detail"), {"path": "M/PID"},
                                   engine, parent=window, theme="silver")
    window.faceplates.append((("PID",), faceplate))
    edit = faceplate.findChild(QLineEdit)
    assert edit is not None
    edit.setText("-1.")
    edit.setSelection(0, 2)
    selection = (edit.selectionStart(), edit.selectedText())

    def unexpected_poll(*args, **kwargs):
        pytest.fail("Restyling sampled process state and can replace pending input")

    monkeypatch.setattr(faceplate, "refresh", unexpected_poll)
    assert window.choose_theme("dark")
    assert edit.text() == "-1."
    assert (edit.selectionStart(), edit.selectedText()) == selection
    assert faceplate.palette_roles is THEMES["dark"]


def test_historian_shell_updates_without_replacing_chart(stations, app):
    window = stations()
    history = window.open_process_history()
    chart = history._chart
    history.show()
    window.choose_theme("dark")
    app.processEvents()
    assert history._chart is chart
    assert history.palette().color(QPalette.Window).name() == THEMES["dark"][Role.SURFACE_PANEL].lower()


def test_themed_history_time_window_is_readable_and_plot_menu_stays_hidden(stations, app):
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    window = stations()
    history = window.open_process_history()
    history.show()
    window.choose_theme("hpgray")
    app.processEvents()
    combo = history._chart._window_combo
    assert combo.currentText()
    assert history._chart._plot.ctrlMenu.isWindow()
    assert not history._chart._plot.ctrlMenu.isVisible()
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    text_area = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
    required = combo.fontMetrics().horizontalAdvance(combo.currentText())
    assert text_area.width() >= required, (combo.currentText(), text_area, required)


@pytest.mark.parametrize("invalid", ["removed-theme", ["dark"], 17])
def test_invalid_saved_preference_falls_back_with_visible_explanation(stations, invalid):
    original = stations(default="hpgray")
    original._preferences.setValue("theme", invalid)
    original._preferences.sync()
    original.close()
    reopened = stations(default="hpgray")
    assert reopened.settings.theme == "hpgray"
    assert "default" in reopened.theme_notice.text()


def test_preference_write_failure_keeps_session_and_explains_it(stations):
    class Unwritable:
        def setValue(self, *_):
            raise OSError("test preference failure")
    window = stations()
    window._preferences = Unwritable()
    assert window.choose_theme("dark")
    assert window.themes.current == "dark"
    assert "could not be saved" in window.theme_notice.text()


def test_failed_consumer_rolls_back_all_surfaces_without_saving(stations):
    from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme

    class FailingTool(QWidget):
        def apply_operator_theme(self, name):
            if name == "dark":
                raise RuntimeError("test consumer failure")

    window = stations()
    tool = FailingTool(window)
    bind_operator_theme(tool)
    window.show_display("Plant")
    view = window.view
    assert not window.choose_theme("dark")
    assert window.settings.theme == window.themes.current == "silver"
    assert view.palette_roles is THEMES["silver"]
    assert tool.palette().color(QPalette.Window).name() == THEMES["silver"][Role.SURFACE_PANEL].lower()
    assert not window._preferences.contains("theme")
    assert "could not be applied" in window.theme_notice.text()
    window.themes.set_theme("dark")
    assert window.themes.current == window.settings.theme == "silver"
    assert tool.palette().color(QPalette.Window).name() == THEMES["silver"][Role.SURFACE_PANEL].lower()


def test_theme_roles_have_readable_text_and_fixed_alarm_semantics():
    from azeo_control_trainer.core.hmi.theme.vision import luminance
    for theme in ("silver", "dark", "hpgray"):
        palette = THEMES[theme]
        for text, background in ((Role.TEXT, Role.SURFACE_PANEL), (Role.TEXT_DIM, Role.SURFACE_FIELD),
                                 (Role.ACTION, Role.SURFACE_PANEL_ALT), (Role.ON_SELECTION, Role.SELECTION)):
            light, dark = sorted((luminance(palette[text]), luminance(palette[background])), reverse=True)
            assert (light + 12.75) / (dark + 12.75) >= 4.5, (theme, text, background)
        for invariant in (Role.ALARM_P1, Role.ALARM_P2, Role.BAR_PV):
            assert palette[invariant] == THEMES["silver"][invariant]


def test_dark_diagnostic_tabs_render_light_legible_labels(app):
    from azeo_control_trainer.core.hmi.pvms.detail_ui import _PIDDiagnosticsTabs
    from azeo_control_trainer.core.hmi.theme.widgets import apply_widget_theme
    panel = _PIDDiagnosticsTabs(THEMES["dark"])
    try:
        apply_widget_theme(panel, "dark", basic=True)
        panel.resize(350, 300)
        panel.show()
        app.processEvents()
        bar = panel.tabs.tabBar()
        image = bar.grab().toImage()
        for index in range(bar.count()):
            area = bar.tabRect(index).adjusted(5, 4, -5, -4)
            bright = sum(min(image.pixelColor(x, y).getRgb()[:3]) > 180
                         for y in range(area.top(), area.bottom())
                         for x in range(area.left(), area.right()))
            assert bright > 12, (index, bright)
    finally:
        panel.close()
        panel.deleteLater()


def test_dark_measured_loop_rethemes_painted_alarm_well_and_setpoint(app):
    from azeo_control_trainer.core.hmi.pvms.loop_surface import LoopFaceplateSurface
    surface = LoopFaceplateSurface(THEMES["dark"])
    try:
        surface.show()
        app.processEvents()
        image = surface.grab().toImage()
        assert image.pixelColor(4, 450).name() == THEMES["dark"][Role.SURFACE_PANEL].lower()
        bright = sum(min(image.pixelColor(x, y).getRgb()[:3]) > 180
                     for y in range(155, 174) for x in range(5, 68))
        assert bright > 12
        surface.apply_theme(THEMES["hpgray"])
        assert surface.VALUE.name() == THEMES["hpgray"][Role.HEADING].lower()
        assert surface.BLUE.name() == THEMES["hpgray"][Role.BAR_PV].lower()
    finally:
        surface.close()
        surface.deleteLater()


def test_all_registered_faces_update_in_place_without_polling(stations, app, monkeypatch):
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget

    station = stations()
    count = 0
    for (block, role, variant), kind in registry.all_classes().items():
        if role not in ("faceplate", "detail"):
            continue
        engine = BindingEngine(LiveGraphSource(lambda: {}))
        widget = PvmFaceplateWidget(kind, {key: f"M/{block}_{key}" for key in kind.PARAMS}, engine,
                                   theme="silver", parent=station, live=False)
        widget.show()
        app.processEvents()
        original_size, document = widget.size(), widget.bound.copy()
        def unexpected_poll():
            raise AssertionError("theme switch polled process data")
        monkeypatch.setattr(widget, "refresh", unexpected_poll)
        for theme in ("dark", "hpgray", "azeo_live", "silver"):
            assert widget.apply_theme(theme)
            app.processEvents()
            assert widget.size() == original_size, (block, role, variant)
            assert widget.bound == document
            assert widget.palette_roles is THEMES[theme]
            assert widget.palette().color(QPalette.Window).name() == THEMES[theme][Role.SURFACE_PANEL].lower()
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        count += 1
    assert count >= 25


def test_help_keeps_topic_selection_and_station_scope(stations, app):
    window = stations()
    help_window = window.open_suite_help()
    assert help_window.show_topic("OPERATOR_THEMES.md#choose-an-hmi-theme")
    help_window.browser.find("Charcoal Dark")
    selection = help_window.browser.textCursor().selectedText()
    assert selection == "Charcoal Dark"
    window.choose_theme("dark")
    assert help_window.current_topic_key == "OPERATOR_THEMES.md#choose-an-hmi-theme"
    assert help_window.browser.textCursor().selectedText() == selection
    assert help_window.palette().color(QPalette.Window).name() == THEMES["dark"][Role.SURFACE_PANEL].lower()
    help_window.close()


def test_closed_details_leave_theme_fanout_and_release_bindings(stations, app):
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
    window = stations()
    window.show_display("Plant")
    engine = window.view.engine
    before = engine.monitored_count
    for _ in range(4):
        window.open_detail(Pvm("loop", "", "PID", "faceplate", {"path": "M/PID"}))
        assert window.faceplates
        window.faceplates[-1][1].close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not window.faceplates
        assert not window.findChildren(PvmFaceplateWidget)
        assert engine.monitored_count == before


def test_theme_preserves_historian_data_gaps_and_review_interval(stations, app):
    import math
    window = stations()
    point = window.historian.add_point("M/PID/PV", module="M")
    for seconds, value, quality in ((0, 10, "GOOD"), (1, math.nan, "BAD"), (2, 12, "GOOD")):
        point.sample(seconds, value, quality)
    view = window.open_process_history("M")
    view._timer.stop()
    view._refresh()
    chart = view._chart
    chart.set_review_range(0, 2)
    pen = chart._pens[point.path]
    curve, color, interval = pen._curve, pen.color, chart._plot.vb.viewRange()[0]
    data = curve.getData()
    window.choose_theme("dark")
    assert chart._pens[point.path] is pen
    assert pen._curve is curve and pen.color == color
    assert chart._plot.vb.viewRange()[0] == interval
    import numpy as np
    assert np.array_equal(curve.getData()[0], data[0], equal_nan=True)
    assert np.array_equal(curve.getData()[1], data[1], equal_nan=True)
    assert math.isnan(point.values[1])
