"""The shared icon set: every requested name exists, icons are distinct, DPI-sharp and badged."""
from pathlib import Path
import os
import re
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.presentation import icon_set  # noqa: E402
from azeo_control_trainer.core.presentation.brand import ICON_FAMILIES  # noqa: E402
from azeo_control_trainer.core.presentation.studio_icons import _DRAW_MAP, studio_icon  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "azeo_control_trainer"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_painter_name_and_every_requested_name_has_a_shape():
    missing = sorted(name for name in _DRAW_MAP if not icon_set.has_icon(name))
    assert not missing, missing
    requested = set()
    for source in SRC.rglob("*.py"):
        if source.name in ("studio_icons.py", "icon_set.py"):
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        requested.update(re.findall(r'(?:studio_icon|draw_icon|_draw_icon|_icon)\("([a-z_]+)"', text))
    unknown = sorted(name for name in requested if not icon_set.has_icon(name))
    assert not unknown, unknown


def test_every_icon_has_a_colour_family_and_families_are_the_brand_palette():
    assert set(icon_set.FAMILY_OF.values()) <= set(ICON_FAMILIES)
    unfamilied = sorted(name for name in icon_set.names() if name not in icon_set.FAMILY_OF)
    assert not unfamilied, unfamilied
    assert icon_set.FAMILY_OF["alarm"] == "alarm"
    assert [name for name, family in icon_set.FAMILY_OF.items() if family == "alarm"] == ["alarm"]


def test_command_icons_are_distinct_in_both_styles(app):
    for style in (icon_set.STYLE_COLOUR, icon_set.STYLE_LINE):
        seen = {}
        for name in icon_set.names():
            if name in ("em_child", "pvm_class", "faceplate_class", "folder", "show"):
                continue  # deliberate aliases of module, pvm, faceplate, project, watch
            pixels = bytes(icon_set.render_pixmap(name, 16, style).toImage().constBits())
            assert pixels not in seen, (style, name, seen.get(pixels))
            seen[pixels] = name


def test_icons_follow_the_device_pixel_ratio(app, monkeypatch):
    monkeypatch.setattr(icon_set, "_device_pixel_ratio", lambda: 2.0)
    pixmap = icon_set.render_pixmap("save", 20)
    assert pixmap.devicePixelRatioF() == 2.0
    assert pixmap.width() == 40
    icon = icon_set.make_icon("save", 20)
    assert not icon.isNull()


def test_shared_entry_point_and_badges(app):
    direct = bytes(icon_set.render_pixmap("procedure", 16).toImage().constBits())
    through_studio = bytes(studio_icon("procedure", 16).pixmap(16, 16).toImage().constBits())
    assert direct == through_studio
    plain = bytes(icon_set.render_pixmap("module", 16).toImage().constBits())
    for state in icon_set.STATE_BADGES:
        badged = bytes(icon_set.render_pixmap("module", 16, state=state).toImage().constBits())
        assert badged != plain, state
    assert studio_icon("unknown_glyph", 16).pixmap(16, 16).toImage() != studio_icon("procedure", 16).pixmap(16, 16).toImage()


def test_line_style_is_available_for_comparison(app, monkeypatch):
    monkeypatch.setenv("AZEO_ICON_STYLE", "line")
    assert icon_set.default_style() == icon_set.STYLE_LINE
    line = bytes(icon_set.render_pixmap("save", 20).toImage().constBits())
    colour = bytes(icon_set.render_pixmap("save", 20, icon_set.STYLE_COLOUR).toImage().constBits())
    assert line != colour


def test_proposal_sheet_renders_from_the_shared_set(app, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import render_icon_proposal
    for style in (icon_set.STYLE_LINE, icon_set.STYLE_COLOUR):
        output = render_icon_proposal.render(tmp_path / f"{style}.png", style)
        assert output.exists() and output.stat().st_size > 10_000
