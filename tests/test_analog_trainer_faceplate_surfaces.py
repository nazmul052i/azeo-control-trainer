"""Interaction contracts for the measured analogue and trainer surfaces."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.analog_fb_surfaces import (
    AnalogOutputFaceplateSurface,
    PulseInputFaceplateSurface,
)
from azeo_control_trainer.core.hmi.pvms.analog_surface import (
    AnalogFaceplateSurface,
)
from azeo_control_trainer.core.hmi.pvms.trainer_faceplate_surfaces import (
    RampSoakFaceplateSurface,
    TotalizerFaceplateSurface,
)
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _centre(rect) -> QPoint:
    centre = rect.center()
    return QPoint(round(centre.x()), round(centre.y()))


def _paint(surface) -> None:
    """Populate paint-defined command geometry exactly as the live host does."""
    pixmap = QPixmap(surface.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    surface.render(pixmap)


def test_tagao_moves_alarm_hotspots_with_the_visible_table(app) -> None:
    surface = AnalogOutputFaceplateSurface(THEMES[DEFAULT_THEME])

    assert surface._region("mini") is None
    assert surface._region("alarm_up").getRect() == (187.0, 327.0, 14.0, 14.0)
    assert surface._region("alarm_down").getRect() == (
        187.0, 344.0, 14.0, 14.0)
    assert surface._hit(QPointF(190, 96)) is None


def test_ai_actual_mode_label_and_drop_arrow_have_separate_lanes(app) -> None:
    surface = AnalogFaceplateSurface(THEMES[DEFAULT_THEME])
    badge, text, arrow = surface.actual_mode_geometry()

    assert badge.right() < text.left()
    assert text.right() < arrow.left()
    assert arrow.right() <= surface.DESIGN_WIDTH
    assert text.width() >= 47


def test_pi_exposes_only_its_visible_return_button(app) -> None:
    surface = PulseInputFaceplateSurface(THEMES[DEFAULT_THEME])
    actions: list[str] = []
    writes: list[tuple[str, object]] = []
    surface.action_requested.connect(actions.append)
    surface.write_requested.connect(lambda key, value: writes.append((key, value)))

    # These locations are interactive on Loop_fp, but PI_fp does not paint
    # those controls.  A derived surface must not inherit invisible targets.
    for point in (QPoint(190, 96), QPoint(35, 130), QPoint(150, 210)):
        QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=point)
    assert actions == []
    assert writes == []

    surface.set_available_actions({"faceplate"})
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=_centre(surface._return_region()),
    )
    assert actions == ["faceplate"]


@pytest.mark.parametrize(
    ("surface_type", "command"),
    ((TotalizerFaceplateSurface, "cmd.reset"),
     (RampSoakFaceplateSurface, "cmd.start"),
     (RampSoakFaceplateSurface, "cmd.stop"),
     (RampSoakFaceplateSurface, "cmd.reset")),
)
def test_trainer_adaptation_commands_match_visible_buttons(
        app, surface_type, command) -> None:
    surface = surface_type(THEMES[DEFAULT_THEME])
    surface.set_write_permissions({command})
    writes: list[tuple[str, object]] = []
    surface.write_requested.connect(lambda key, value: writes.append((key, value)))
    _paint(surface)

    rect, _key, _value = surface._commands[command]
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=_centre(rect))
    assert writes == [(command, True)]


def test_trainer_commands_follow_host_permissions_without_loop_ghosts(app) -> None:
    surface = RampSoakFaceplateSurface(THEMES[DEFAULT_THEME])
    writes: list[tuple[str, object]] = []
    surface.write_requested.connect(lambda key, value: writes.append((key, value)))
    surface.show()
    app.processEvents()
    _paint(surface)

    start = _centre(surface._commands["cmd.start"][0])
    surface.set_write_permissions(set())
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=start)
    assert writes == []

    # This is Loop_fp's hidden SP-up region, not a trainer command.
    QTest.mouseMove(surface, QPoint(35, 140))
    assert surface.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert surface._hover is None

    surface.set_write_permissions({"cmd.start"})
    QTest.mouseMove(surface, start)
    assert surface.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=start)
    assert writes == [("cmd.start", True)]
    surface.close()
