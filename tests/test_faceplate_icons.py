"""The faceplate symbol set is original, complete, distinct and sharp at its native sizes."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.pvms import faceplate_icons as icons  # noqa: E402
from azeo_control_trainer.core.presentation.brand import ICON_FAMILIES  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def render(name, size, **options):
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    icons.draw_faceplate_icon(painter, QRectF(0, 0, size, size), name, **options)
    painter.end()
    return bytes(pixmap.toImage().constBits())


def test_every_symbol_paints_something_distinct_at_its_native_size(app):
    seen = {}
    for name in icons.FACEPLATE_ICONS:
        size = icons.SPECIAL_SYMBOL_SIZES[name][0]
        pixels = render(name, size, button=name in icons.SPECIAL_SYMBOL_BUTTONS)
        assert pixels != render("__blank__", size), f"{name} paints only the fallback frame"
        assert len(set(pixels)) > 8, f"{name} paints nothing at {size} px"
        assert pixels not in seen, (name, seen.get(pixels))
        seen[pixels] = name
    # The 16 px marks also stay distinct from each other.
    assert render("alarm", 16) != render("alarm_help", 16) != render("simulate", 16)


def test_button_shell_pressed_and_disabled_states_are_visible(app):
    plain = render("module_detail", 31, button=True)
    assert plain != render("module_detail", 31, button=False)
    assert plain != render("module_detail", 31, button=True, pressed=True)
    assert plain != render("module_detail", 31, button=True, enabled=False)


def test_symbol_colours_come_from_the_suite_families(app):
    assert set(icons._families()) == set(ICON_FAMILIES)
    for family in ("document", "hardware", "graphics", "tool", "alarm", "stop"):
        light, base, dark = icons._family(family)
        assert light.name().upper() == ICON_FAMILIES[family][0].upper()
        assert dark.name().upper() == ICON_FAMILIES[family][2].upper()


def test_palette_preview_matches_the_canvas_painter(app):
    preview = icons.special_symbol_pixmap("process_history", 48, 48)
    assert preview.width() == 48 and not preview.isNull()
    direct = QPixmap(48, 48)
    direct.fill(QColor(0, 0, 0, 0))
    painter = QPainter(direct)
    icons.draw_faceplate_icon(painter, QRectF(0, 0, 48, 48), "process_history", button=True)
    painter.end()
    assert bytes(preview.toImage().constBits()) == bytes(direct.toImage().constBits())
