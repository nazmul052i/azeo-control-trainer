"""The measured DC_fp surface remains data-honest and spatially stable."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.binding.result import BindingResult
from azeo_control_trainer.core.hmi.pvms.device_surface import (
    DeviceFaceplateSurface,
)
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
from azeo_control_trainer.core.strategy.model.terminal import Quality


@pytest.fixture(scope="module")
def app():
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


def _bound(**results):
    return {
        key: SimpleNamespace(result=value)
        for key, value in results.items()
    }


def _good(value, **kwargs):
    return BindingResult(value=value, quality=Quality.GOOD, **kwargs)


def _centre(rect):
    center = rect.center()
    return QPoint(round(center.x()), round(center.y()))


def test_dc_fp_uses_the_measured_250_by_560_coordinate_contract(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    assert surface.size() == QSize(250, 560)
    assert surface.minimumSizeHint() == QSize(250, 560)

    assert surface.status_icon_geometry("interlocked").getRect() \
        == (14.0, 45.0, 20.0, 20.0)
    assert surface.status_icon_geometry("bypassed").top() == 73.0
    assert surface.status_icon_geometry("no_permit").top() == 101.0
    assert surface.status_icon_geometry("simulate").left() == 216.0
    assert surface.state_button_geometry("cmd.start").getRect() \
        == (88.0, 87.0, 76.0, 22.0)
    assert surface.reset_geometry().getRect() \
        == (174.0, 306.0, 55.0, 27.0)
    assert surface.trend_geometry().getRect() \
        == (25.0, 398.0, 200.0, 114.0)
    assert surface.action_geometry("detail").center().x() == 82.0
    assert surface.action_geometry("faceplate").center().x() == 168.0


def test_refresh_consumes_only_the_existing_device_faceplate_keys(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    surface.refresh(_bound(**{
        "field.value": _good(True),
        "state.value": _good(1),
        "state.running": _good(False),
        "state.fail": _good(False),
        "state.fail_code": _good(0),
        "state.locked": _good(False),
        "state.paused": _good(True),
        "elapsed": _good(3.25),
        "time.start_limit": _good(10.0),
        "time.stop_limit": _good(12.0),
        "mode": _good(
            "AUTO", mode_target="CAS", mode_actual="AUTO",
            mode_normal="AUTO"),
    }))

    assert surface.pv_text == "RUNNING"
    assert surface.state_name == "Starting"
    assert surface.transition_limit == 10.0
    assert surface.elapsed == 3.25
    assert surface.target_mode == "CAS"
    assert surface.actual_mode == "AUTO"
    assert surface.target_mode_mismatch
    assert surface.actual_mode_mismatch
    assert surface.failure_text == "Clear"
    assert surface.paused is True

    # These five states do exist in the vendor figure, but no current
    # DeviceFaceplate binding can answer them.  Unknown must not become False.
    assert surface.bypassed is None
    assert surface.no_permit is None
    assert surface.simulate_active is None
    assert surface.accepted is None
    assert surface.delay_remaining is None
    assert surface.interlocked is False


def test_interlocked_and_reset_marks_require_explicit_device_state(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    surface.refresh(_bound(**{
        "field.value": _good(False),
        "state.value": _good(5),
        "state.locked": _good(False),
        "state.fail": _good(True),
        "state.fail_code": _good(8),
    }))
    assert surface.interlocked is True
    assert surface.failure_text == "Shutdown"
    assert not surface.reset_visible

    surface.refresh(_bound(**{
        "field.value": _good(False),
        "state.value": _good(6),
        "state.locked": _good(True),
        "state.fail": _good(False),
        "state.fail_code": _good(0),
    }))
    assert surface.interlocked is False
    assert surface.state_name == "Locked"
    assert surface.reset_visible

    surface.refresh({})
    assert surface.state_code is None
    assert surface.state_name == "\u2014"
    assert surface.interlocked is None
    assert surface.failure_text == "\u2014"
    assert not surface.reset_visible


def test_state_and_footer_controls_emit_intents_without_mutating_feedback(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    surface.set_write_permissions({"cmd.start", "cmd.stop", "cmd.reset"})
    writes = []
    actions = []
    surface.write_requested.connect(lambda key, value: writes.append((key, value)))
    surface.action_requested.connect(actions.append)

    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.state_button_geometry("cmd.start")))
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.state_button_geometry("cmd.stop")))
    assert writes == [("cmd.start", True), ("cmd.stop", True)]

    # Reset has no hotspot until DC_STATE actually says Locked.
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.reset_geometry()))
    assert writes == [("cmd.start", True), ("cmd.stop", True)]
    surface.refresh(_bound(**{
        "state.value": _good(6),
        "state.locked": _good(True),
    }))
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.reset_geometry()))
    assert writes[-1] == ("cmd.reset", True)

    # Unserviceable footer icons disappear together with their hotspots.
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.action_geometry("detail")))
    assert actions == []
    surface.set_available_actions({"detail", "faceplate"})
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.action_geometry("detail")))
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.action_geometry("faceplate")))
    assert actions == ["detail", "faceplate"]


def test_device_commands_remove_hotspots_when_host_refuses_writes(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    surface.refresh(_bound(**{
        "state.value": _good(6),
        "state.locked": _good(True),
    }))
    writes = []
    surface.write_requested.connect(lambda key, value: writes.append((key, value)))
    surface.set_write_permissions({"cmd.start"})

    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.state_button_geometry("cmd.stop")))
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.reset_geometry()))
    assert writes == []

    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface.state_button_geometry("cmd.start")))
    assert writes == [("cmd.start", True)]


def test_host_checker_permissions_reach_the_whole_surface(app):
    surface = DeviceFaceplateSurface(THEMES[DEFAULT_THEME])
    specs = tuple(SimpleNamespace(key=key, writable=True) for key in (
        "cmd.start", "cmd.stop"))
    bound = {
        key: SimpleNamespace(path=f"MOTOR/DC1/{terminal}")
        for key, terminal in (
            ("cmd.start", "START_CMD"), ("cmd.stop", "STOP_CMD"))
    }
    host = SimpleNamespace(
        pvm=SimpleNamespace(bindings=specs),
        bound=bound,
        write_handler=object(),
        write_checker=lambda path: SimpleNamespace(
            success=path.endswith("START_CMD"), error="Denied by role"),
        write_controls={},
        visual=surface,
    )

    PvmFaceplateWidget._refresh_write_permissions(host)

    assert surface.writable_keys == frozenset({"cmd.start"})
