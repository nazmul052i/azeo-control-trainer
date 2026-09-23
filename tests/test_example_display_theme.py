"""Every shipped plant example must remain readable as the station changes theme."""
# ruff: noqa: E402
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem, StaticItem
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import THEMES
from tools.generate_azeo_plant_displays import build_displays, DISPLAY_NAMES, UNIT_L2


@pytest.fixture(scope="module")
def app():
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


def example(name):
    return next(d for d in build_displays() if d.name == name)


@pytest.mark.parametrize("name", DISPLAY_NAMES)
def test_example_uses_theme_roles_for_neutral_artwork(name):
    document = example(name)
    assert not document.background, "The authored silver background defeats station themes"
    for item in document.items:
        assert not any(item.get(k) for k in ("text_color", "line", "fill")), item["id"]
        if item["kind"] == "text":
            assert item["text_role"] in Role._value2member_map_


@pytest.mark.parametrize("name", DISPLAY_NAMES)
def test_example_routes_are_valid_and_static_artwork_follows_theme(app, name):
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor
    view = PvmDisplayView(example(name).to_dict(), lambda: {}, live=False)
    try:
        scene = view.scene()
        document = view.display.to_dict()
        index = {getattr(item.pvm, "id", "") if hasattr(item, "pvm") else item.data["id"]: item
                 for item in scene.items() if hasattr(item, "pvm") or isinstance(getattr(item, "data", None), dict)}
        for theme in ("silver", "dark", "hpgray", "azeo_live"):
            view.apply_theme(theme)
            assert view.scene() is scene
            assert view.display.to_dict() == document
            for item in scene.items():
                if isinstance(item, PipeItem):
                    assert item.route_status == "ok", (name, item.data["id"], item.route_message)
                    assert item.line_colour().name() == THEMES[theme][Role.EQUIPMENT].lower()
                    for side, point, neighbor in (("a", item._points[0], item._points[1]),
                                                   ("b", item._points[-1], item._points[-2])):
                        normal = port_anchor(index[item.data[side]], item.data[side + "_side"]).normal
                        dx, dy = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0)}[normal]
                        assert (neighbor.x() - point.x()) * dx + (neighbor.y() - point.y()) * dy > 0, (
                            name, item.data["id"], "Pipe enters through the equipment body")
                elif isinstance(item, StaticItem) and item.data.get("kind") == "text":
                    assert item.text_colour().name() == THEMES[theme][Role(item.data["text_role"])].lower()
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", ("silver", "dark", "hpgray"))
def test_motor_letter_uses_the_theme_text(app, theme):
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QImage, QPainter
    from azeo_control_trainer.core.hmi.pvms.symbols import renderer
    palette = THEMES[theme]
    svg = renderer("motor", line=palette[Role.EQUIPMENT], fill=palette[Role.EQUIPMENT_FILL], text=palette[Role.TEXT])
    image = QImage(108, 108, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        svg.render(painter, QRectF(0, 0, 108, 108))
    finally:
        painter.end()
    opaque = [image.pixelColor(x, y) for x in range(108) for y in range(108)
              if image.pixelColor(x, y).alpha() == 255]
    assert len(opaque) > 6000, "The symbol must actually render"
    assert any(c.name() == palette[Role.TEXT].lower() for c in opaque), "Motor letter must remain readable in every theme"


def test_feed_pump_identification_does_not_overlap():
    from PySide6.QtCore import QRectF
    items = {item["id"]: item for item in example("U100 - L2 Feed Preparation").items}
    caption, tag = (items[key] for key in ("u100_p101a_label", "u100_p101b_tag"))
    boxes = [QRectF(item["x"], item["y"], item["w"], item["h"]) for item in (caption, tag)]
    assert not boxes[0].intersects(boxes[1])


@pytest.mark.parametrize("width", [132, 266])
def test_hp_readouts_leave_room_for_status_icons(app, monkeypatch, width):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from azeo_control_trainer.core.hmi.pvms.hp import painters

    # Record the actual painter transform: a clipping-only fix would hide OUT.
    marks = []
    scales = []
    def record(painter, rect):
        marks.append(painter.worldTransform().mapRect(rect))
        scales.append(painter.worldTransform().m11())
    monkeypatch.setattr(painters, "_field_row",
                        lambda p, item, r, y, *args: record(p, QRectF(r.x(), y, r.width(), painters.ROW_H)))
    monkeypatch.setattr(painters, "draw_combination_bar", lambda p, r, *args: record(p, r))
    monkeypatch.setattr(painters, "draw_out_bar", lambda p, r, *args: record(p, r))
    class Item:
        _palette = THEMES["dark"]
        binding = None
        rows = {}
        pvm = type("Pvm", (), {"params": {}})()
        def rect(self):
            return QRectF(0, 0, width, 64)
    image = QImage(width, 90, QImage.Format_ARGB32)
    painter = QPainter(image)
    try:
        painters.paint_hp_combination(painter, Item())
    finally:
        painter.end()
    assert len(marks) == 5
    assert all(0 <= mark.top() < mark.bottom() <= 46 for mark in marks), marks
    if width == 266:
        assert scales == [1] * 5, "Overview numbers were shrunk despite available horizontal space"


def test_explicit_artwork_colors_still_override_roles(app):
    item = StaticItem({"kind": "text", "text": "State", "text_role": "TEXT",
                       "text_color": "#123456", "line_role": "LINE",
                       "line": "#654321", "fill_role": "SURFACE_PANEL",
                       "fill": "#345678"}, THEMES["dark"])
    assert item.text_colour().name() == "#123456"
    assert item.line_colour().name() == "#654321"
    assert item.fill_colour().name() == "#345678"


def test_studio_can_choose_roles_save_and_undo(app, tmp_path):
    from PySide6.QtCore import QSettings
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "preferences"))
    window = HmiStudioWindow(lambda: {}, tmp_path)
    try:
        studio = window.current()
        item = studio.add_static("text", 100, 100, 250, 40)
        item.data["text_color"] = "#123456"
        studio.selection.replace((item,), primary=item)
        studio.pane.show_item(item)
        combo = studio.pane.item_fields["text_role"]
        combo.setCurrentIndex(combo.findData("TEXT"))
        assert item.data["text_role"] == "TEXT"
        assert "text_color" not in item.data
        studio._sync_document()
        document = studio.display
        restored = PvmDisplay.from_dict(document.to_dict())
        assert any(row.get("text_role") == "TEXT" for row in restored.items)
        identity = item.data["id"]
        studio.undo()
        studio._sync_document()
        restored_item = next(row for row in studio.display.items if row["id"] == identity)
        assert restored_item["text_color"] == "#123456"
        assert "text_role" not in restored_item
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
        QSettings.setDefaultFormat(previous_format)


def test_targeted_example_release_preserves_other_pages_and_is_idempotent(tmp_path):
    from tools.generate_azeo_plant_displays import install, publish_operator
    other = tmp_path / "Other display" / "draft.json"
    other.parent.mkdir()
    other.write_text('{"display":"Other display"}', encoding="utf-8")
    assignments = tmp_path / "_accepted.json"
    assignments.write_text('{"CON-01":{}}', encoding="utf-8")
    names = (UNIT_L2["U300"],)
    assert len(install(tmp_path, names=names)) == 1
    assert len(publish_operator(tmp_path, names=names)) == 1
    assert publish_operator(tmp_path, names=names) == ()
    assert other.read_text(encoding="utf-8") == '{"display":"Other display"}'
    assert assignments.read_text(encoding="utf-8") == '{"CON-01":{}}'
