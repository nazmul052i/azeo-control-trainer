"""Faceplate parts an author can insert instead of redrawing.

Building an authored faceplate from scratch meant redrawing every part
from primitives — a PV bar is a track, a fill rectangle and an animation
descriptor normalizing EU0..EU100, and getting it subtly wrong is quiet.
`create_faceplate_blueprint` already knew how to build all of it, as one
monolithic scaffold.

These check the three things that make cutting it into parts safe:

1. **The blueprint did not change.** It is now composed from the same
   sections, item for item, so existing authored classes and the measured
   204x468 scaffold are unaffected.
2. **An insert is ONE undo step** and never collides with a previous
   insert's element identities.
3. **A part that needs a class property the class does not declare says
   so.** The binding is legitimately unresolved, but silence is not.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(app, tmp_path):
    from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
    return UserPvmLibrary(tmp_path)


def _strip(items):
    """Items without the per-call identity the library assigns."""
    out = []
    for item in items:
        copy = dict(item)
        copy.pop("source_element_id", None)
        out.append(copy)
    return out


# ------------------------------------------------- the blueprint holds
def test_the_blueprint_is_composed_from_the_sections(app, library):
    """Item for item: the scaffold must not have moved."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    library.create_faceplate_blueprint("Probe")
    stored = _strip(library.entries["Probe"]["items"])
    composed = _strip(faceplate_sections.blueprint_items())
    assert stored == composed


def test_the_blueprint_keeps_its_measured_shape(app, library):
    library.create_faceplate_blueprint("Probe")
    items = library.entries["Probe"]["items"]
    assert len(items) == 19
    surface = next(i for i in items if i["id"] == "fp_surface")
    assert (surface["w"], surface["h"]) == (204, 468), (
        "the measured faceplate body is a product contract")
    assert [i["id"] for i in items][:4] == [
        "fp_surface", "fp_module", "fp_title", "fp_desc"]


def test_every_section_is_reachable_from_the_blueprint_order(app):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert set(faceplate_sections.BLUEPRINT_ORDER) \
        == set(faceplate_sections.SECTIONS)


# --------------------------------------------------------- the parts
@pytest.mark.parametrize("key", [
    "surface", "title", "value", "pv_bar", "setpoint", "out_bar",
    "trend", "alarms", "unit"])
def test_each_section_builds_placeable_items(app, key):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections
    from azeo_control_trainer.core.hmi.pvms.elements import (
        DATA_ELEMENT_KINDS)

    items = faceplate_sections.items_for(key)
    assert items, key
    known = set(DATA_ELEMENT_KINDS) | {
        "rect", "text", "datalink", "user_entry", "symbol", "line"}
    for item in items:
        assert item["kind"] in known, f"{key}: {item['kind']}"
        assert item["id"].startswith("fp_")
        assert item.get("w", 0) > 0 and item.get("h", 0) > 0


def test_a_section_lands_where_it_is_placed(app):
    """A trend lives at y=303 in the scaffold; inserting one at the
    click point must not drop it 303 pixels lower."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert faceplate_sections.origin("trend") == (8.0, 303.0)
    placed = faceplate_sections.items_at("trend", 500, 400, prefix="x_")
    assert (placed[0]["x"], placed[0]["y"]) == (500.0, 400.0)


def test_relative_geometry_survives_placement(app):
    """The PV bar must stay centred in its own channel after a move."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    home = {i["id"]: i for i in faceplate_sections.items_for("pv_bar")}
    away = {i["id"]: i
            for i in faceplate_sections.items_at("pv_bar", 300, 300)}
    for name in home:
        assert away[name]["x"] - home[name]["x"] == pytest.approx(
            away["fp_pv_track"]["x"] - home["fp_pv_track"]["x"])


def test_the_pv_bar_keeps_its_eu_normalizing_descriptor(app):
    """The part exists because this descriptor is easy to get wrong."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    bar = next(i for i in faceplate_sections.items_for("pv_bar")
               if i["id"] == "fp_pv_bar")
    animation = bar["props"]["fill_pct"]
    assert animation["kind"] == "animation"
    assert animation["path"] == "Pvm.PVPath"
    assert animation["input_start"] == "Pvm.EU0"
    assert animation["input_end"] == "Pvm.EU100"
    assert (animation["output_start"], animation["output_end"]) == (0, 100)


def test_an_unknown_section_is_refused(app):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    with pytest.raises(KeyError):
        faceplate_sections.items_for("no_such_section")


# ------------------------------------------- declared-property report
def test_a_section_reports_the_properties_the_class_lacks(app):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert faceplate_sections.missing_properties(
        "pv_bar", ["PVPath"]) == ("EU0", "EU100")
    assert faceplate_sections.missing_properties(
        "pv_bar", ["PVPath", "EU0", "EU100"]) == ()
    assert faceplate_sections.missing_properties("surface", []) == ()


def test_the_blueprint_configuration_satisfies_every_section(app):
    """The default faceplate class must not ship a part it cannot bind."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections
    from azeo_control_trainer.azeo_graphics_designer.configurator.designer \
        import PvmConfigDesigner

    config = PvmConfigDesigner._faceplate_configuration("Probe")
    declared = [prop.name for group in config.groups
                for prop in group.properties]
    for key in faceplate_sections.SECTIONS:
        assert faceplate_sections.missing_properties(key, declared) == (), (
            f"{key} reads a property the blueprint class does not declare")


def test_faceplate_parts_have_distinct_code_native_previews(app):
    from PySide6.QtCore import QByteArray, QBuffer
    from azeo_control_trainer.azeo_graphics_designer.component_icons import (
        element_preview)
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    def pixels(key):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.WriteOnly)
        element_preview(key, 80, 52).save(buffer, "PNG")
        buffer.close()
        return bytes(data)

    keys = tuple(faceplate_sections.SECTIONS) + tuple(
        faceplate_sections.LIVE_SECTIONS)
    previews = {key: pixels(key) for key in keys}
    assert len(set(previews.values())) == len(previews), (
        "faceplate parts must not all use the generic component glyph")


# ------------------------------------------------ insertion in Studio
@pytest.fixture
def studio(app):
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import (
        PvmStudio)

    root = Path(tempfile.mkdtemp(prefix="fpsec"))
    (root / "displays" / "pvm").mkdir(parents=True)
    widget = PvmStudio(lambda: {}, str(root), display_name="Probe")
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


def _sections_on(studio):
    return [i for i in studio._static_items()]


def test_inserting_a_section_is_one_undo_step(app, studio):
    before = len(_sections_on(studio))
    count, _missing = studio.insert_faceplate_section("value", 40, 40)
    assert count == 4
    assert len(_sections_on(studio)) == before + 4
    assert studio.undo()
    assert len(_sections_on(studio)) == before, (
        "one insert must cost exactly one Ctrl+Z")


def test_two_inserts_do_not_share_element_identities(app, studio):
    studio.insert_faceplate_section("value", 10, 10)
    studio.insert_faceplate_section("value", 10, 200)
    ids = [i.data.get("id") for i in _sections_on(studio)
           if i.data.get("id")]
    assert len(ids) == len(set(ids)), (
        "a second Value row must not overwrite the first one's identity")


def test_inserting_reports_properties_the_class_has_not_declared(app,
                                                                 studio):
    """A plain display declares no Pvm.* properties, so every typed
    binding in the part is unresolved — and must be named."""
    _count, missing = studio.insert_faceplate_section("pv_bar", 20, 20)
    assert set(missing) == {"PVPath", "EU0", "EU100"}


def test_an_unknown_section_inserts_nothing(app, studio):
    assert studio.insert_faceplate_section("nope", 0, 0) == (0, ())
