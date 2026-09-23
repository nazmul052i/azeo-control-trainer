"""Reusable PVM/faceplate class contracts and class-master authoring."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    INTERNAL,
    PvmConfiguration,
    PvmProperty,
    PropertyGroup,
)
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _application():
    return QApplication.instance() or QApplication([])


def _graph():
    block = BlockRegistry().create("PID", "PID1")
    assert block is not None
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    return graph


def _window(tmp_path):
    _application()
    return HmiStudioWindow(
        lambda: {"UNIT100": _graph()}, tmp_path, area_name="Plant")


def test_class_interface_metadata_is_backward_compatible_and_typed():
    old_document = {
        "pvm_class": "LegacyCard",
        "groups": [{"name": "Basic", "properties": [
            {"name": "path", "type": "Control Tag"},
        ]}],
    }
    legacy = PvmConfiguration.from_dict(old_document)
    assert legacy.to_dict() == old_document
    assert legacy.public_properties()[0].name == "path"
    assert legacy.internal_properties() == []
    assert legacy.primary_drop_target() is None

    config = PvmConfiguration("LoopCard", [PropertyGroup("Interface", [
        PvmProperty(
            "ControlTag", "Function Block Reference", required=True,
            drop_target=True, accepted_block_types=["pid", "PID_AT"]),
        PvmProperty("NormalizedPV", "Number", scope=INTERNAL),
    ])])
    round_trip = PvmConfiguration.from_dict(config.to_dict())
    assert round_trip.issues() == ()
    assert round_trip.drop_target_for("PID").name == "ControlTag"
    assert round_trip.drop_target_for("AI") is None
    assert [prop.name for prop in round_trip.public_properties()] == [
        "ControlTag"]
    assert [prop.name for prop in round_trip.internal_properties()] == [
        "NormalizedPV"]


def test_invalid_drop_contract_blocks_class_use():
    wrong_type = PvmConfiguration("Broken", [PropertyGroup("Interface", [
        PvmProperty("Tag", "String", drop_target=True,
                    accepted_block_types=["PID"]),
    ])])
    messages = [str(issue) for issue in wrong_type.issues()]
    assert any("primary drop target must be" in message
               for message in messages)

    duplicate = PvmConfiguration("Ambiguous", [PropertyGroup(
        "Interface", [
            PvmProperty("A", "Control Tag", drop_target=True,
                        accepted_block_types=["PID"]),
            PvmProperty("B", "Control Tag", drop_target=True,
                        accepted_block_types=["PID"]),
        ])])
    assert duplicate.primary_drop_target() is None
    assert any("only one primary drop target" in str(issue)
               for issue in duplicate.issues())


def test_control_block_drop_places_compatible_authored_class(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("LoopCard", [
        {"kind": "rect", "id": "surface", "x": 0, "y": 0,
         "w": 160, "h": 60},
        {"kind": "text", "id": "tag", "x": 8, "y": 8,
         "w": 144, "h": 20, "text": "Pvm.ControlTag"},
    ], definition_kind="pvm", master_size=(160, 60)) is not None
    config = PvmConfiguration("LoopCard", [PropertyGroup("Interface", [
        PvmProperty(
            "ControlTag", "Function Block Reference", required=True,
            drop_target=True, accepted_block_types=["PID"]),
        PvmProperty("Helper", "String", scope=INTERNAL,
                    default="not shown at placement"),
    ])])
    config.save(tmp_path / "_pvmcfg")

    window = _window(tmp_path)
    studio = window.current()
    assert "LoopCard" in studio.authored_classes_for("PID")
    assert "LoopCard" not in studio.authored_classes_for("AI")
    studio.visual_choices["PID"] = ("authored", "LoopCard")
    assert studio.place_block("UNIT100/PID1", "PID", 40, 50) == 2

    members = [item for item in studio._static_items()
               if item.data.get("user_pvm") == "LoopCard"]
    assert len(members) == 2
    assert all(item.data["pvm_choices"]["ControlTag"] == "UNIT100/PID1"
               for item in members)
    text_item = next(item for item in members
                     if item.data.get("kind") == "text")
    assert text_item.data["text"] == "UNIT100/PID1"

    built = studio._build_user_pvm_choices_dialog(members[0])
    assert built is not None
    dialog, _editors, _choices, properties = built
    assert [prop.name for prop in properties] == ["ControlTag"]
    assert "Helper" not in {
        label.text() for label in dialog.findChildren(QLabel)}
    assert "ControlTag *" in {
        label.text() for label in dialog.findChildren(QLabel)}
    dialog.close()

    # Palette placement is allowed while authoring, but Publish preflight
    # must not let an unresolved required interface masquerade as complete.
    assert studio.place_user_pvm("LoopCard", 240, 50) == 2
    assert "LoopCard: required ControlTag" in studio.validate()
    window.close()


def test_class_master_uses_entry_page_and_preserves_whitespace(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("WhitespaceCard", [
        {"kind": "rect", "id": "body", "x": 15, "y": 12,
         "w": 80, "h": 32},
    ], definition_kind="pvm", master_size=(180, 90)) is not None
    PvmConfiguration("WhitespaceCard", [PropertyGroup("Interface", [
        PvmProperty("Tag", "Control Tag", required=True,
                    drop_target=True, accepted_block_types=["PID"]),
    ])]).save(tmp_path / "_pvmcfg")

    window = _window(tmp_path)
    studio = window.edit_user_pvm_layout("WhitespaceCard")
    assert studio.edited_user_class_name() == "WhitespaceCard"
    assert (studio.display.width, studio.display.height) == (180, 90)
    assert studio.display.safe_margin == 0
    assert not studio.pane.class_interface_panel.isHidden()
    assert studio.pane.display_kind_chip.text() == "PVM CLASS"
    assert studio.pane.display_title.text() == "WhitespaceCard"
    assert "Primary drop target: Tag" in (
        studio.pane.class_interface_summary.text())
    window._sync_chrome()
    index = window.tabs.indexOf(studio)
    assert window.tabs.tabText(index) == "WhitespaceCard [PVM Class]"
    assert not window._ribbon_buttons["display.publish"].isEnabled()
    assert studio.display.name not in window._display_hierarchy()

    studio.save_draft()
    assert not (tmp_path / studio.display.name / "draft.json").exists()
    saved = UserPvmLibrary(tmp_path).entries["WhitespaceCard"]
    assert (saved["w"], saved["h"]) == (180.0, 90.0)
    body = next(item for item in saved["items"]
                if item.get("id") == "body")
    assert (body["x"], body["y"]) == (15, 12)
    window.close()
