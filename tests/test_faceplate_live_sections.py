"""Hosted faceplate sections: the rules an author cannot draw.

A condition table decides that a TRIPPED condition reads ACTIVE while a
satisfied permissive reads OK. A sequencer body decides which of sixteen
rows exist and which are on. Neither is drawable, and this repo has
already had two condition tables that disagreed about the inversion.

So these placements HOST the shipped `Section` widget rather than
copying it. What that has to buy:

1. the real widget renders, with real values, inside the display scene;
2. one control tag generates the sequencer's thirty-three binding paths,
   because entering them by hand is the reason nobody built an SFC
   faceplate — and graphics scripts cannot loop either;
3. the paths resolve through a class's typed properties, or the part
   would work on a plain display and be permanently unbound inside the
   authored class it exists for;
4. no stray native top-level window appears (rule 50).
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
def sequencer(app):
    """A real SEQ block with six configured steps, three of them on."""
    from azeo_control_trainer.core.strategy.blocks.dv_seq_blocks import (
        SequencerBlock)
    from azeo_control_trainer.core.strategy.model.strategy_graph import (
        StrategyGraph)
    from azeo_control_trainer.core.strategy.model.terminal import Quality

    steps = ["Purge nitrogen", "Charge solvent", "Heat to 80 C",
             "Hold 30 min", "Cool to 40 C", "Transfer to D-201"]
    graph = StrategyGraph(name="SEQ-101")
    block = SequencerBlock("SEQ1")
    for index, text in enumerate(steps, start=1):
        block.config.params[f"DESC_OUT{index}"] = text
    block._apply_config()
    graph.add_block(block)
    block.outputs["STATE"].value = 3
    block.outputs["STATE"].status = Quality.GOOD
    for index in range(1, len(steps) + 1):
        terminal = block.outputs.get(f"OUT_D{index}")
        if terminal is not None:
            terminal.value = index <= 3
            terminal.status = Quality.GOOD
    return graph, steps


def _view(app, document, graphs):
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
        PvmDisplayView)

    view = PvmDisplayView(document, lambda: graphs, theme="hpgray",
                          live=True)
    view.resize(360, 440)
    view.show()
    app.processEvents()
    view.refresh()
    for _ in range(3):
        app.processEvents()
    return view


def _document(**overrides):
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay

    item = {"kind": "faceplate_section", "id": "sec",
            "section": "state_list", "x": 20, "y": 20,
            "w": 300, "h": 380}
    item.update(overrides)
    return PvmDisplay(name="Section probe", items=[item], width=340,
                      height=420, level=2, show_tag="none").to_dict()


# ----------------------------------------------------- path generation
def test_one_control_tag_generates_the_whole_sequencer_map(app):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    paths = faceplate_sections.sequence_paths("SEQ-101", "SEQ1")
    assert len(paths) == 1 + 2 * faceplate_sections.SEQUENCE_ROWS == 33
    assert paths["state.name"] == "SEQ-101/SEQ1/STATE"
    assert paths["row1"] == "SEQ-101/SEQ1/CONFIG/DESC_OUT1"
    assert paths["row1.state"] == "SEQ-101/SEQ1/OUT_D1"
    assert paths["row16"] == "SEQ-101/SEQ1/CONFIG/DESC_OUT16"


def test_a_step_description_is_a_config_parameter_not_a_terminal(app):
    """`{path}/CONFIG/<NAME>` — the binding-integrity rule this repo
    already learned once with alarm limits."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    paths = faceplate_sections.sequence_paths("SEQ-101")
    assert "/CONFIG/" in paths["row4"]
    assert "/CONFIG/" not in paths["row4.state"]


def test_an_empty_control_tag_generates_nothing(app):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert faceplate_sections.sequence_paths("") == {}
    assert faceplate_sections.sequence_paths("   ") == {}


# ------------------------------------------------------- live render
def test_the_hosted_section_renders_real_sequencer_values(app, sequencer):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections
    from PySide6.QtCore import Qt

    graph, steps = sequencer
    paths = faceplate_sections.sequence_paths("SEQ-101", "SEQ1")
    view = _view(app, _document(paths=paths), {"SEQ-101": graph})
    try:
        item = next(i for i in view.scene().items()
                    if getattr(i, "data", None)
                    and isinstance(i.data, dict)
                    and i.data.get("kind") == "faceplate_section")
        assert item.binding_error == ""
        assert len(item.bindings) == 33
        widget = item._section_widget
        # Qt normalizes AllButtons to the platform's supported standard-button
        # mask, so test the interaction contract rather than its enum width.
        assert item._section_proxy.acceptedMouseButtons() & Qt.LeftButton
        assert type(widget).__name__ == "StateList", (
            "the SHIPPED section must be hosted, not a copy of it")
        assert widget.state.text() == "3"
        assert widget.table.rowCount() == len(steps), (
            "only configured steps are listed; the other ten are absent, "
            "not blank rows")
        assert widget.table.item(0, 0).text() == "Purge nitrogen"
        assert widget.table.item(0, 1).text() == "ON"
        assert widget.table.item(4, 1).text() == "", (
            "step 5 is off and must not read ON")
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_hosting_creates_no_native_top_level_window(app, sequencer):
    """Rule 50: every extra visible widget is owned by a graphics proxy."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections
    from PySide6.QtWidgets import QApplication

    graph, _steps = sequencer
    before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
    view = _view(app, _document(
        paths=faceplate_sections.sequence_paths("SEQ-101")),
        {"SEQ-101": graph})
    try:
        after = {id(w) for w in QApplication.topLevelWidgets()
                 if w.isVisible() and w is not view
                 and w.graphicsProxyWidget() is None}
        assert not (after - before)
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_a_missing_or_unknown_section_is_reported_not_crashed(app):
    view = _view(app, _document(section="no_such_section", paths={"a": "b"}),
                 {})
    try:
        item = next(i for i in view.scene().items()
                    if getattr(i, "data", None)
                    and isinstance(i.data, dict)
                    and i.data.get("kind") == "faceplate_section")
        assert "no_such_section" in item.binding_error
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_a_section_without_paths_says_so(app):
    view = _view(app, _document(paths={}), {})
    try:
        item = next(i for i in view.scene().items()
                    if getattr(i, "data", None)
                    and isinstance(i.data, dict)
                    and i.data.get("kind") == "faceplate_section")
        assert "binding paths" in item.binding_error
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


# ------------------------------------------- resolution inside a class
def test_paths_resolve_through_the_classes_typed_properties(app, tmp_path):
    """Without this the part works on a plain display and is
    permanently unbound in the authored class it exists for."""
    from azeo_control_trainer.core.hmi.pvms.configurator.model import (
        PvmConfiguration, PropertyGroup, PvmProperty)
    from azeo_control_trainer.core.hmi.pvms.user_library import (
        UserPvmLibrary)

    library = UserPvmLibrary(tmp_path)
    library.add("SeqBody", [
        {"kind": "faceplate_section", "id": "body",
         "section": "state_list", "x": 0, "y": 0, "w": 300, "h": 380,
         "paths": {"state.name": "Pvm.ControlTag/SEQ1/STATE",
                   "row1": "Pvm.ControlTag/SEQ1/CONFIG/DESC_OUT1"}}],
        folder="My Faceplates", definition_kind="faceplate")

    config = PvmConfiguration(
        pvm_class="SeqBody",
        groups=[PropertyGroup(
            name="Binding",
            properties=[PvmProperty(name="ControlTag", ptype="String",
                                    default="SEQ-101")])])
    items = library.instantiate("SeqBody", 0, 0, config=config,
                                choices={})
    body = next(i for i in items if i.get("kind") == "faceplate_section")
    assert body["paths"]["state.name"] == "SEQ-101/SEQ1/STATE"
    assert body["paths"]["row1"] == "SEQ-101/SEQ1/CONFIG/DESC_OUT1"


# --------------------------------------------------- Studio insertion
@pytest.fixture
def studio(app):
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import (
        PvmStudio)

    root = Path(tempfile.mkdtemp(prefix="livesec"))
    (root / "displays" / "pvm").mkdir(parents=True)
    widget = PvmStudio(lambda: {}, str(root), display_name="Probe")
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


def _sections(studio):
    return [i for i in studio._static_items()
            if i.data.get("kind") == "faceplate_section"]


def test_inserting_a_sequencer_body_is_one_undo_step(app, studio):
    count, keys = studio.insert_live_section(
        "state_list", 10, 10, control_tag="SEQ-101")
    assert count == 1
    assert len(keys) == 33
    assert len(_sections(studio)) == 1
    assert studio.undo()
    assert _sections(studio) == []


def test_a_blank_control_tag_binds_through_the_class_property(app, studio):
    studio.insert_live_section("state_list", 10, 10)
    paths = _sections(studio)[0].data["paths"]
    assert paths["state.name"].startswith("Pvm.ControlTag/"), (
        "an authored class binds per instance, not to a fixed module")


def test_an_unknown_live_section_inserts_nothing(app, studio):
    assert studio.insert_live_section("nope", 0, 0) == (0, ())


def test_the_condition_table_is_offered_and_places(app, studio):
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert "conditions" in faceplate_sections.LIVE_SECTIONS
    count, _keys = studio.insert_live_section(
        "conditions", 10, 10, control_tag="MTR-102")
    assert count == 1
    assert _sections(studio)[0].data["section"] == "conditions"


def test_condition_table_is_a_real_bounded_proxy_without_process_commands(
        app, studio):
    from PySide6.QtCore import Qt

    studio.insert_live_section(
        "conditions", 10, 10, control_tag="MTR-102")
    item = _sections(studio)[0]
    widget = item._section_widget
    proxy = item._section_proxy
    studio.canvas.viewport().update()
    app.processEvents()
    assert widget.reset_button is None
    assert proxy.widget() is widget
    assert proxy.geometry().width() <= item.rect().width()
    assert proxy.geometry().height() <= item.rect().height()
    assert proxy.acceptedMouseButtons() == Qt.NoButton


def test_refresh_signature_includes_quality_and_status_fields(app):
    from types import SimpleNamespace
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.strategy.model.terminal import (
        LimitStatus, Quality)

    binding = SimpleNamespace(result=BindingResult(
        value=5, quality=Quality.GOOD, limit=LimitStatus.NOT_LIMITED))
    good = StaticItem._section_result_signature("pv", binding)
    binding.result = BindingResult(
        value=5, quality=Quality.BAD, limit=LimitStatus.HIGH_LIMITED,
        forced=True, last_good_value=5, last_good_at=12.0)
    bad = StaticItem._section_result_signature("pv", binding)
    assert bad != good, "equal values with changed quality must repaint"


def test_bad_quality_sequencer_flag_never_reads_on(app):
    from types import SimpleNamespace
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    from azeo_control_trainer.core.hmi.pvms.faceplate_fb import StateList
    from azeo_control_trainer.core.hmi.theme.tokens import (
        DEFAULT_THEME, THEMES)
    from azeo_control_trainer.core.strategy.model.terminal import Quality

    widget = StateList(THEMES[DEFAULT_THEME])
    widget.refresh({
        "state.name": SimpleNamespace(result=BindingResult(
            value="1", quality=Quality.GOOD)),
        "row1": SimpleNamespace(result=BindingResult(
            value="Start", quality=Quality.GOOD)),
        "row1.state": SimpleNamespace(result=BindingResult(
            value=True, quality=Quality.BAD)),
    })
    assert widget.table.item(0, 1).text() == ""
    widget.deleteLater()


def test_failed_section_refresh_is_visible_and_retried(app, sequencer):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    graph, _steps = sequencer
    view = _view(app, _document(
        paths=faceplate_sections.sequence_paths("SEQ-101")),
        {"SEQ-101": graph})
    try:
        item = next(i for i in view.scene().items()
                    if isinstance(getattr(i, "data", None), dict)
                    and i.data.get("kind") == "faceplate_section")
        item._section_signature = None

        def broken(_bound):
            raise RuntimeError("probe failure")

        item._section_widget.refresh = broken
        image = QImage(400, 440, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        item._paint_live_section(painter, QRectF(item.rect()))
        painter.end()
        assert "probe failure" in item.binding_error
        assert item._section_signature is None, (
            "a failed signature must remain pending for the next refresh")
    finally:
        view.close()
        view.deleteLater()


def test_interactive_sections_are_not_offered_as_painted_controls(app):
    """`mode` and `selector` carry buttons. A painted button is a dead
    button, which is worse than an absent one."""
    from azeo_control_trainer.core.hmi.pvms import faceplate_sections

    assert "mode" not in faceplate_sections.LIVE_SECTIONS
    assert "selector" not in faceplate_sections.LIVE_SECTIONS
