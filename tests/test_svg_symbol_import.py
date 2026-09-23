"""Importing an engineer's own SVG as a first-class equipment symbol.

Four defects this file exists to keep fixed, each verified by running the
code rather than by reading it:

1. An imported symbol ignored the theme. Vendored artwork follows
   EQUIPMENT/EQUIPMENT_FILL; an import kept its authored colours, so it
   stayed bright beside themed equipment on a dark station.
2. An import named after vendored artwork SHADOWED it. `symbol_path`
   prefers user files, so importing `pump.svg` repainted every pump on
   every display in the project, with no edit and no warning.
3. Anything imported. A broken file registered and painted nothing; a
   file carrying `<script>`, `onload=` and an external `<image href>`
   registered and rendered.
4. A name could leave `_library/svg/` entirely.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TWO_TONE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<rect x="2" y="2" width="36" height="36" fill="#ffcc00" '
    'stroke="#803300" stroke-width="2"/>'
    '<circle cx="20" cy="20" r="5" fill="#222222"/>'
    "</svg>"
)

HOSTILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" '
    'onload="alert(1)"><script>fetch("http://example.com")</script>'
    '<image xlink:href="http://example.com/x.png" width="10" height="10"/>'
    '<style>@import url("http://example.com/x.css"); .a{fill:red}</style>'
    '<rect width="10" height="10" fill="#888888" onclick="x()"/></svg>'
)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def symbols(app):
    """The symbol catalog, restored after each test.

    CATALOG and USER_SYMBOLS are module state shared with the operator
    renderer; a test that leaves an import behind would change what a
    later test's display resolves.
    """
    from azeo_control_trainer.core.hmi.pvms import symbols as module
    catalog = dict(module.CATALOG)
    user = dict(module.USER_SYMBOLS)
    meta = dict(module.USER_SYMBOL_META)
    root = module._user_symbol_root
    try:
        yield module
    finally:
        module.CATALOG.clear()
        module.CATALOG.update(catalog)
        module.USER_SYMBOLS.clear()
        module.USER_SYMBOLS.update(user)
        module.USER_SYMBOL_META.clear()
        module.USER_SYMBOL_META.update(meta)
        module._user_symbol_root = root
        for cache in (module._renderers, module._content_boxes,
                      module._outline_points, module._connection_ports):
            cache.clear()


def _render(svg_bytes, size=200):
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg_bytes))
    assert renderer.isValid(), "prepared SVG must render in Qt"
    image = QImage(size, size, QImage.Format_RGBA8888)
    image.fill(Qt.white)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def _hex(image, x, y):
    from PySide6.QtGui import QColor
    return QColor(image.pixel(x, y)).name().upper()


def test_malformed_connection_metadata_fails_closed(symbols, monkeypatch):
    monkeypatch.setattr(symbols, "_symbol_source", lambda name: b"<svg><broken>")
    assert symbols.connection_ports("malformed-test-symbol") == {}


# --------------------------------------------------------------- refusals
@pytest.mark.parametrize("label,text", [
    ("not XML", "<svg><unclosed>"),
    ("not an svg root", "<html><body/></html>"),
    ("no scalable box", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'),
    ("nothing drawable",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
     "<title>empty</title></svg>"),
])
def test_unusable_files_are_refused_with_a_reason(app, label, text):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    with pytest.raises(svg_import.SvgImportError) as refusal:
        svg_import.prepare(text)
    assert str(refusal.value), f"{label} must explain itself to the engineer"


def test_script_handlers_and_external_references_are_stripped(app):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    result = svg_import.prepare(HOSTILE)
    for forbidden in ("script", "onload", "onclick", "@import",
                      "example.com"):
        assert forbidden not in result.svg
    assert result.removed, "the engineer is told what was removed"
    # The drawing itself survives the scrub.
    assert "rect" in result.svg


def test_external_urls_are_removed_from_every_attribute_form(app):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<defs><linearGradient id="local"><stop offset="1"/></linearGradient></defs>'
        '<rect width="10" height="10" '
        'style="fill:url(https://example.com/a.svg#x)" '
        'filter="url(file:///tmp/filter.svg#x)"/>'
        '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>'
        '<circle r="2" fill="url(#local)"/></svg>')
    result = svg_import.prepare(source)
    assert "example.com" not in result.svg
    assert "file:///" not in result.svg
    assert "data:image" not in result.svg
    assert "url(#local)" in result.svg


def test_entities_and_excessive_nesting_are_refused(app):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    entity = ('<!DOCTYPE svg [<!ENTITY x "boom">]>'
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
              '<text>&x;</text></svg>')
    with pytest.raises(svg_import.SvgImportError, match="DOCTYPE"):
        svg_import.prepare(entity)
    deep = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
            + '<g>' * (svg_import.MAX_SVG_DEPTH + 1)
            + '<rect width="1" height="1"/>'
            + '</g>' * (svg_import.MAX_SVG_DEPTH + 1) + '</svg>')
    with pytest.raises(svg_import.SvgImportError, match="nesting"):
        svg_import.prepare(deep)


def test_published_revision_embeds_the_exact_custom_symbol(
        app, tmp_path, symbols):
    from azeo_control_trainer.core.hmi.pvms import svg_import
    from azeo_control_trainer.core.hmi.pvms.publishing import (
        DisplayStore, PvmDisplay)
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
        PvmDisplayView)

    library = tmp_path / "_library" / "svg"
    library.mkdir(parents=True)
    svg_import.atomic_write_svg(
        library / "feed_skid.svg", svg_import.prepare(TWO_TONE).svg)
    store = DisplayStore(tmp_path)
    display = PvmDisplay(
        name="Pinned", width=120, height=120,
        items=[{"kind": "symbol", "id": "s1", "symbol": "feed_skid",
                "x": 10, "y": 10, "w": 80, "h": 80}])
    store.publish(display)
    document = store.published_document("Pinned")
    assert "feed_skid" in document["symbol_assets"]
    (library / "feed_skid.svg").unlink()

    view = PvmDisplayView(document, lambda: {}, live=False)
    internal = ""
    try:
        item = next(item for item in view.scene().items()
                    if isinstance(getattr(item, "data", None), dict))
        internal = item.data["symbol"]
        assert internal.startswith("__document_")
        assert symbols.renderer(internal) is not None
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()
    assert internal not in symbols.DOCUMENT_SYMBOLS


def test_revert_restores_the_revisions_symbol_not_the_mutable_library(
        app, tmp_path, symbols):
    from azeo_control_trainer.core.hmi.pvms import svg_import
    from azeo_control_trainer.core.hmi.pvms.publishing import (
        DisplayStore, PvmDisplay)

    library = tmp_path / "_library" / "svg"
    library.mkdir(parents=True)
    target = library / "skid.svg"
    first_svg = svg_import.prepare(TWO_TONE).svg
    svg_import.atomic_write_svg(target, first_svg)
    display = PvmDisplay(
        name="Pinned", items=[{"kind": "symbol", "symbol": "skid"}])
    store = DisplayStore(tmp_path)
    first = store.publish(display)
    assert "+ custom symbol skid" in first["diff"]
    pinned_first = store.revision_document("Pinned", first["rev"])[
        "symbol_assets"]["skid"]

    second_source = TWO_TONE.replace('r="5"', 'r="7"')
    svg_import.atomic_write_svg(target, svg_import.prepare(second_source).svg)
    second = store.publish(display)
    assert "~ custom symbol artwork changed skid" in second["diff"]
    pinned_second = store.revision_document("Pinned", second["rev"])[
        "symbol_assets"]["skid"]
    assert pinned_second != pinned_first

    reverted = store.revert("Pinned", first["rev"])
    pinned_revert = store.revision_document("Pinned", reverted["rev"])[
        "symbol_assets"]["skid"]
    assert pinned_revert == pinned_first


def test_the_original_is_never_modified(app, tmp_path, symbols):
    source = tmp_path / "original.svg"
    source.write_text(TWO_TONE, encoding="utf-8")
    before = source.read_bytes()
    from azeo_control_trainer.core.hmi.pvms import svg_import

    svg_import.prepare(source.read_text(encoding="utf-8"))
    assert source.read_bytes() == before


# ----------------------------------------------------------------- theme
def test_an_imported_symbol_follows_the_theme(app, tmp_path, symbols):
    """Defect 1: vendored artwork re-themes and an import did not."""
    prepared = _prepare_to(tmp_path / "skid.svg")
    plain = tmp_path / "plain.svg"
    plain.write_text(TWO_TONE, encoding="utf-8")

    assert symbols._themed_bytes(plain) == plain.read_bytes(), (
        "unprepared artwork is inert — this is the defect being fixed")
    themed = symbols._themed_bytes(prepared, line="#FF0000", fill="#00FF00")
    other = symbols._themed_bytes(prepared, line="#0000FF", fill="#FFFF00")
    assert themed != prepared.read_bytes()
    assert themed != other, "two themes must paint the import differently"


def test_light_body_and_dark_ink_take_different_theme_roles(app, tmp_path,
                                                            symbols):
    """Two-tone artwork stays two-tone: flattening it to one colour would
    make every imported symbol a silhouette."""
    prepared = _prepare_to(tmp_path / "skid.svg")
    line, fill = "#FF0000", "#00FF00"
    image = _render(symbols._themed_bytes(prepared, line=line, fill=fill))
    assert _hex(image, 40, 40) == fill.upper()      # light body
    assert _hex(image, 100, 100) == line.upper()    # dark ink
    assert _hex(image, 100, 11) == line.upper()     # stroke


def test_authored_none_and_line_weights_survive(app):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    result = svg_import.prepare(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<rect width="4" height="4" fill="none" stroke="#000" '
        'stroke-width="3"/></svg>')
    assert 'fill="none"' in result.svg, "AGENTS.md keeps intentional none"
    assert 'stroke-width="3"' in result.svg, "the author chose that weight"


def test_artwork_with_its_own_theme_block_is_left_alone(app, symbols):
    """Re-importing vendored artwork must not re-theme what is themed."""
    from azeo_control_trainer.core.hmi.pvms import svg_import

    vendored = symbols.symbol_path("vessel")
    result = svg_import.prepare(vendored.read_text(encoding="utf-8"))
    assert result.preauthored
    assert not result.remapped


# ------------------------------------------------------------------ names
def test_a_vendored_name_is_never_taken_by_an_import(app, tmp_path, symbols):
    """Defect 2: the quiet one. `pump` would have repainted every pump."""
    from azeo_control_trainer.core.hmi.pvms import svg_import

    original = symbols.symbol_path("pump")
    impostor = _prepare_to(tmp_path / "impostor.svg")

    assert symbols.register_user_symbol("pump", impostor) is False
    assert symbols.symbol_path("pump") == original

    free = svg_import.safe_symbol_name("pump", taken=symbols.VENDORED_NAMES)
    assert free not in symbols.VENDORED_NAMES
    assert symbols.register_user_symbol(free, impostor) is True
    assert symbols.symbol_path("pump") == original


@pytest.mark.parametrize("raw,expected_absent", [
    ("..\\..\\escape", ("\\", "/", "..")),
    ("with/slash", ("/",)),
    ("   ", ()),
    ("my nice pump!!", ("!", " ")),
])
def test_a_name_cannot_leave_the_library_folder(app, raw, expected_absent):
    """Defect 4."""
    from azeo_control_trainer.core.hmi.pvms import svg_import

    name = svg_import.safe_symbol_name(raw)
    assert name, "a name is always produced"
    for token in expected_absent:
        assert token not in name
    assert (Path("/library/svg") / f"{name}.svg").parent \
        == Path("/library/svg")


def test_reserved_windows_names_are_avoided(app):
    from azeo_control_trainer.core.hmi.pvms import svg_import

    for reserved in ("CON", "nul", "com1"):
        assert svg_import.safe_symbol_name(reserved).casefold() \
            not in svg_import._RESERVED_NAMES


# -------------------------------------------------------------- catalogue
def test_an_import_joins_the_equipment_catalog(app, tmp_path, symbols):
    """The Equipment palette is built from CATALOG, so this is what puts
    an imported symbol on the stencil beside the vendored artwork."""
    prepared = _prepare_to(tmp_path / "skid.svg")
    assert symbols.register_user_symbol(
        "skid", prepared, title="Feed Skid", category="Skids")
    assert symbols.CATALOG["skid"][1] == "Feed Skid"
    assert symbols.CATALOG["skid"][2] == "SKIDS"
    assert symbols.renderer("skid") is not None
    assert symbols.aspect("skid") == pytest.approx(1.0)
    # It anchors on its own ink like vendored artwork, not on the rect.
    assert symbols.outline_points("skid")


def test_unregistering_clears_every_cache(app, tmp_path, symbols):
    prepared = _prepare_to(tmp_path / "skid.svg")
    symbols.register_user_symbol("skid", prepared)
    assert symbols.renderer("skid") is not None
    assert symbols.unregister_user_symbol("skid")
    assert "skid" not in symbols.CATALOG
    assert symbols.renderer("skid") is None


def test_opening_another_project_drops_the_first_projects_symbols(
        app, tmp_path, symbols):
    """A display must not resolve a symbol its own project never had."""
    first, second = tmp_path / "a" / "svg", tmp_path / "b" / "svg"
    for folder, name in ((first, "alpha"), (second, "beta")):
        folder.mkdir(parents=True)
        _prepare_to(folder / f"{name}.svg")

    assert symbols.load_user_symbols(first) == 1
    assert "alpha" in symbols.USER_SYMBOLS
    assert symbols.load_user_symbols(second) == 1
    assert "beta" in symbols.USER_SYMBOLS
    assert "alpha" not in symbols.USER_SYMBOLS
    assert "alpha" not in symbols.CATALOG


def test_manually_dropped_unsafe_svg_never_joins_the_palette(
        app, tmp_path, symbols):
    folder = tmp_path / "svg"
    folder.mkdir()
    (folder / "unsafe.svg").write_text(
        '<!DOCTYPE svg [<!ENTITY x "boom">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        '<text>&x;</text></svg>', encoding="utf-8")
    assert symbols.load_user_symbols(folder) == 0
    assert "unsafe" not in symbols.CATALOG


def test_reimport_requires_explicit_replacement(app, tmp_path, symbols):
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import (
        PvmStudio)

    source = tmp_path / "source.svg"
    source.write_text(TWO_TONE, encoding="utf-8")
    fake_studio = SimpleNamespace(store=SimpleNamespace(root=tmp_path / "store"))
    first = PvmStudio.import_svg_file(fake_studio, source, name="skid")
    assert first
    before = first.path.read_bytes()
    refused = PvmStudio.import_svg_file(fake_studio, source, name="skid")
    assert not refused
    assert "confirm replacement" in refused.error
    assert first.path.read_bytes() == before
    replaced = PvmStudio.import_svg_file(
        fake_studio, source, name="skid", replace=True)
    assert replaced and replaced.replaced


def test_the_sidecar_round_trips_labels_and_categories(app, tmp_path,
                                                       symbols):
    folder = tmp_path / "svg"
    folder.mkdir()
    _prepare_to(folder / "skid.svg")
    symbols.register_user_symbol("skid", folder / "skid.svg",
                                 title="Feed Skid", category="Skids")
    symbols.save_user_symbol_index(folder)
    symbols.unregister_user_symbol("skid")
    symbols._user_symbol_root = None

    assert symbols.load_user_symbols(folder) == 1
    assert symbols.CATALOG["skid"][1] == "Feed Skid"
    assert symbols.CATALOG["skid"][2] == "SKIDS"


def test_a_file_deleted_outside_the_studio_stops_being_a_symbol(
        app, tmp_path, symbols):
    folder = tmp_path / "svg"
    folder.mkdir()
    path = _prepare_to(folder / "skid.svg")
    assert symbols.load_user_symbols(folder) == 1
    path.unlink()
    assert symbols.load_user_symbols(folder) == 0
    assert "skid" not in symbols.CATALOG


# ------------------------------------------------- the placement override
def test_the_placement_artwork_override_round_trips_and_stays_absent(app):
    """It must not change a byte of any display that does not use it."""
    from dataclasses import replace
    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import (
        pvm_from_dict)

    placement = Pvm(id="p1", pvm_class="", block_type="DEVCTL",
                    role="dynamo_inline", params={"path": "XV-101"})
    assert placement.symbol == ""
    assert "symbol" not in placement.to_dict()

    overridden = replace(placement, symbol="skid")
    assert overridden.to_dict()["symbol"] == "skid"
    assert pvm_from_dict(overridden.to_dict()).symbol == "skid"
    assert pvm_from_dict(placement.to_dict()).to_dict() \
        == placement.to_dict()


def _device_display(**overrides):
    """One DEVCTL valve placement — a PVM that draws equipment."""
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay

    placement = {"id": "dev1", "class": "DEVCTL/dynamo_inline",
                 "params": {"path": "XV-101"},
                 "x": 20, "y": 20, "w": 84, "h": 88}
    placement.update(overrides)
    return PvmDisplay(name="Artwork probe", pvms=[placement],
                      width=140, height=140, level=2,
                      show_tag="none").to_dict()


def _view_image(app, document):
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
        PvmDisplayView)

    view = PvmDisplayView(document, lambda: {}, theme="hpgray", live=False)
    view.resize(150, 150)
    view.show()
    app.processEvents()
    view.refresh()
    app.processEvents()
    try:
        return view.grab().toImage()
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def _pixels(image) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def test_a_device_pvm_paints_the_imported_artwork(app, tmp_path, symbols):
    """The whole point of the override: a valve PVM drawing our SVG."""
    symbols.register_user_symbol("feed_skid", _prepare_to(
        tmp_path / "feed_skid.svg"), category="Skids")

    stock = _view_image(app, _device_display())
    overridden = _view_image(app, _device_display(symbol="feed_skid"))
    assert _pixels(stock) != _pixels(overridden), (
        "the placement's artwork override must change what is painted")


def test_an_unknown_artwork_name_falls_back_to_the_class_silhouette(
        app, symbols):
    """A display opened in a project without that symbol must still draw
    its equipment, not a hole where equipment belongs."""
    stock = _view_image(app, _device_display())
    missing = _view_image(app, _device_display(symbol="not_in_this_project"))
    assert _pixels(stock) == _pixels(missing)


def test_the_override_cannot_turn_a_value_card_into_a_silhouette(
        app, tmp_path, symbols):
    """Converting a card would delete the PV/SP/OUT rows the operator
    reads — a change of meaning, not of appearance."""
    symbols.register_user_symbol("feed_skid", _prepare_to(
        tmp_path / "feed_skid.svg"), category="Skids")
    card = {"id": "ai1", "class": "AI/dynamo_inline",
            "params": {"path": "TI-100"},
            "x": 20, "y": 20, "w": 84, "h": 88}
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay

    def document(**extra):
        placement = dict(card, **extra)
        return PvmDisplay(name="Card probe", pvms=[placement], width=140,
                          height=140, level=2, show_tag="none").to_dict()

    assert _pixels(_view_image(app, document())) \
        == _pixels(_view_image(app, document(symbol="feed_skid")))


def _prepare_to(target: Path) -> Path:
    """Write the two-tone probe artwork through the real import doorway."""
    from azeo_control_trainer.core.hmi.pvms import svg_import

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg_import.prepare(TWO_TONE).svg, encoding="utf-8")
    return target
