"""Authored PVM revision and nested-configuration contracts."""
from __future__ import annotations

import json

from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    PvmConfiguration,
    PvmProperty,
    PropertyGroup,
)
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary


def _configuration(name: str, property_name: str, default: str):
    return PvmConfiguration(name, [PropertyGroup("Interface", [
        PvmProperty(property_name, "String", default=default),
    ])])


def test_touch_definition_advances_only_an_authored_class(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Card", [{
        "kind": "text", "id": "caption", "x": 0, "y": 0,
        "w": 80, "h": 20, "text": "Card",
    }]) is not None
    before_id, before_revision = library.definition_metadata("Card")
    source_id = library.entries["Card"]["items"][0]["source_element_id"]

    assert library.touch_definition("Card")
    after_id, after_revision = library.definition_metadata("Card")
    persisted = json.loads(library.path.read_text(encoding="utf-8"))
    assert after_id == before_id
    assert after_revision == before_revision + 1
    assert persisted["Card"]["definition_revision"] == after_revision
    assert persisted["Card"]["items"][0]["source_element_id"] == source_id

    disk_before = library.path.read_bytes()
    assert not library.touch_definition("PID")  # native, not authored here
    assert not library.touch_definition("Missing")
    assert library.path.read_bytes() == disk_before


def test_nested_class_resolves_its_own_configuration_without_writes(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Child", [{
        "kind": "text", "id": "child-caption", "x": 0, "y": 0,
        "w": 100, "h": 20, "text": "Pvm.ChildLabel",
    }]) is not None
    assert library.add("Parent", [{
        "kind": "text", "id": "parent-caption", "x": 0, "y": 0,
        "w": 100, "h": 20, "text": "Pvm.ParentLabel",
    }]) is not None
    assert library.add_nested("Parent", "Child", x=0, y=30)
    parent_config = _configuration(
        "Parent", "ParentLabel", "PARENT DEFAULT")
    child_config = _configuration(
        "Child", "ChildLabel", "CHILD DEFAULT")
    parent_config.save(tmp_path / "_pvmcfg")
    child_config.save(tmp_path / "_pvmcfg")

    files = [library.path, *(tmp_path / "_pvmcfg").glob("*.json")]
    before = {path: path.read_bytes() for path in files}
    placed = library.instantiate(
        "Parent", 20, 40, config=parent_config)

    text = {row.get("text") for row in placed
            if row.get("kind") == "text"}
    assert text == {"PARENT DEFAULT", "CHILD DEFAULT"}
    assert {path: path.read_bytes() for path in files} == before
