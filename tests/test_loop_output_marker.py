"""The painted OUT marker must track the live bar, including limited scales."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.loop_surface import LoopFaceplateSurface
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def painted_output(surface, *, vertical=False):
    image = QImage(203, 537, QImage.Format_ARGB32)
    image.fill(QColor(7, 8, 9))
    painter = QPainter(image)
    try:
        if vertical:
            surface._vertical_bar(painter)
        else:
            surface._output_bar(painter)
    finally:
        painter.end()
    return image


def marker_center(image):
    pixels = [x for x in range(image.width())
              if image.pixelColor(x, 365) == QColor(26, 128, 126)]
    return (min(pixels) + max(pixels)) / 2 if pixels else None


@pytest.mark.parametrize("low,high,output", [
    (0, 100, 0), (0, 100, 52.4), (0, 100, 68.9), (0, 100, 100),
    (20, 100, 55.1), (20, 100, 20), (20, 100, 100),
    (20, 100, 0), (20, 100, 120),
])
def test_painted_marker_tracks_output_and_scale(app, low, high, output):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.output_min, surface.state.output_max = low, high
        surface.state.output = output
        image = painted_output(surface)
        expected = 7 + 189 * max(0, min(1, (output - low) / (high - low)))
        marker = marker_center(image)
        assert marker is not None
        assert marker == pytest.approx(expected, abs=1)
        # Compare actual ink in both rows, not two geometry helpers that could
        # agree while the painter still draws the old constant-position mark.
        filled = [x for x in range(7, 197)
                  if image.pixelColor(x, 352).red() < 30
                  and 100 < image.pixelColor(x, 352).green() < 130]
        if filled:
            assert max(filled) == pytest.approx(marker, abs=1.5)
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()


def test_bad_output_does_not_leave_a_live_triangle(app):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.output = 68.9
        assert marker_center(painted_output(surface)) is not None
        surface.out.bad = True
        assert marker_center(painted_output(surface)) is None
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("low,high,sp", [
    (0, 500, 175), (0, 500, 0), (0, 500, 500),
    (-50, 50, 25), (0, 500, -25), (0, 500, 525),
])
def test_sp_pointer_follows_its_own_value_on_the_pv_scale(app, low, high, sp):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.pv_min, surface.state.pv_max = low, high
        surface.state.sp = sp
        surface.state.pv = (low + high) / 2
        expected = 311 - 188 * max(0, min(1, (sp - low) / (high - low)))
        for output in (0, 52.4, 100):
            surface.state.output = output
            image = painted_output(surface, vertical=True)
            pointer = surface._furniture(Role.TEXT, 255, 255, 255)
            # x=87 crosses only the triangular pointer; x=92 also crosses
            # the scale border at both limits when TEXT and LINE coincide.
            pixels = [y for y in range(113, 322)
                      if image.pixelColor(87, y) == pointer]
            assert pixels
            assert (min(pixels) + max(pixels)) / 2 == pytest.approx(expected, abs=1.5)
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()


def test_sp_pointer_leaves_the_pv_fill_visible(app):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.sp = 50
        surface.state.pv = 75
        surface.state.pv_min, surface.state.pv_max = 0, 100
        image = painted_output(surface, vertical=True)
        pointer = surface._furniture(Role.TEXT, 255, 255, 255)
        assert image.pixelColor(110, 217) != pointer
        ink = [x for x in range(79, 122)
               if image.pixelColor(x, 217) == pointer]
        assert ink and min(ink) >= 86 and max(ink) < 100
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()


def test_vertical_scale_labels_show_both_limits_not_the_current_sp(app, monkeypatch):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.pv_min, surface.state.pv_max = -50, 250
        surface.state.sp = 175
        surface.state.reference_placeholders = False
        labels = []
        monkeypatch.setattr(surface, "_text", lambda _p, rect, text, *args, **kwargs: labels.append((rect.y(), text)))
        painted_output(surface, vertical=True)
        assert (101, "250") in labels
        assert (312, "-50") in labels
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()


def test_working_sp_mark_is_shown_only_when_it_differs_from_sp(app):
    surface = LoopFaceplateSurface(THEMES[DEFAULT_THEME])
    try:
        surface.state.pv_min, surface.state.pv_max = 0, 100
        surface.state.sp = surface.sp_working = 50
        image = painted_output(surface, vertical=True)
        working = surface._furniture(Role.TEXT, 45, 45, 45)
        assert not any(image.pixelColor(x, y) == working
                       for x in range(122, 129) for y in range(212, 222))
        surface.sp_working = 25
        image = painted_output(surface, vertical=True)
        assert any(image.pixelColor(x, y) == working
                   for x in range(122, 129) for y in range(260, 270))
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()
