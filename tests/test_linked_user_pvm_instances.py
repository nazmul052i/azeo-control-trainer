"""Stable identity and connection preservation for authored PVM instances."""
from __future__ import annotations

import copy
import json
import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary


def _application():
    return QApplication.instance() or QApplication([])


def _class_items(*, fill="#B8C7D1"):
    return [
        {"kind": "rect", "id": "body", "x": 10, "y": 5,
         "w": 50, "h": 30, "fill": fill},
        {"kind": "rect", "id": "outlet", "x": 90, "y": 5,
         "w": 30, "h": 30, "fill": "#D4DCE2"},
        {"kind": "pipe", "id": "internal", "a": "body",
         "a_side": "e", "b": "outlet", "b_side": "w"},
    ]


def _studio(tmp_path):
    _application()
    studio = PvmStudio(lambda: {}, tmp_path, display_name="Drawing")
    studio.enter_edit()
    return studio


def _members(studio, group):
    return [item for item in studio._static_items()
            if item.data.get("group") == group]


def test_legacy_library_is_read_only_until_explicit_save(tmp_path):
    path = tmp_path / "_library" / "user_pvms.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "Legacy": {"items": _class_items(), "w": 120, "h": 35,
                   "folder": "My PVMs", "definition_kind": "pvm"},
    }, indent=2), encoding="utf-8")
    before = path.read_bytes()

    library = UserPvmLibrary(tmp_path)
    definition_id, revision = library.definition_metadata("Legacy")
    placed = library.instantiate("Legacy", 40, 50)
    assert path.read_bytes() == before
    assert revision == 0
    assert uuid.UUID(definition_id)
    assert len({row["instance_id"] for row in placed}) == 1
    assert all(uuid.UUID(row["source_element_id"]) for row in placed)

    entry = library.add("Legacy", copy.deepcopy(
        library.entries["Legacy"]["items"]))
    assert entry is not None
    assert entry["definition_id"] == definition_id
    assert entry["definition_revision"] == 1
    first_sources = [row["source_element_id"] for row in entry["items"]]
    entry = library.add("Legacy", copy.deepcopy(entry["items"]))
    assert entry["definition_id"] == definition_id
    assert entry["definition_revision"] == 2
    assert [row["source_element_id"] for row in entry["items"]] \
        == first_sources


def test_rebuild_preserves_external_pipe_by_source_member(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Skid", _class_items()) is not None
    studio = _studio(tmp_path)
    assert studio.place_user_pvm("Skid", 100, 80) == 3
    instance = next(item.data["instance_id"]
                    for item in studio._static_items()
                    if item.data.get("instance_definition") == "Skid")
    group = f"ug_{instance}"
    body = next(item for item in _members(studio, group)
                if item.data.get("pvm_index") == 0)
    old_body_id = body.data["id"]
    body_source = body.data["source_element_id"]
    external = studio.add_static("rect", 260, 80, 40, 30)
    field_pipe = studio.add_pipe(body, "e", external, "w")
    field_pipe_id = field_pipe.data["id"]

    rows = copy.deepcopy(UserPvmLibrary(tmp_path).entries["Skid"]["items"])
    next(row for row in rows if row.get("id") == "body")["fill"] = "#356A8A"
    assert UserPvmLibrary(tmp_path).add("Skid", rows) is not None
    assert studio.refresh_user_pvm_class("Skid") == 1

    body = next(item for item in _members(studio, group)
                if item.data.get("source_element_id") == body_source)
    assert body.data["id"] != old_body_id
    assert body.data["fill"] == "#356A8A"
    same_pipe = next(pipe for pipe in studio._pipe_items()
                     if pipe.data["id"] == field_pipe_id)
    assert same_pipe.data["a"] == body.data["id"]
    assert same_pipe.data["a_instance_id"] == instance
    assert same_pipe.data["a_source_element_id"] == body_source
    assert len([pipe for pipe in studio._pipe_items()
                if pipe.data.get("instance_internal")
                and pipe.data.get("instance_id") == instance]) == 1
    studio.close()


def test_deleted_class_member_leaves_external_pipe_for_verifier(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Skid", _class_items()) is not None
    studio = _studio(tmp_path)
    assert studio.place_user_pvm("Skid", 100, 80) == 3
    member = next(item for item in studio._static_items()
                  if item.data.get("instance_definition") == "Skid"
                  and item.data.get("pvm_index") == 1)
    group = member.data["group"]
    removed_id = member.data["id"]
    removed_source = member.data["source_element_id"]
    external = studio.add_static("rect", 260, 80, 40, 30)
    field_pipe = studio.add_pipe(member, "e", external, "w")

    entry = UserPvmLibrary(tmp_path).entries["Skid"]
    remaining = [copy.deepcopy(row) for row in entry["items"]
                 if row.get("id") == "body"]
    assert UserPvmLibrary(tmp_path).add("Skid", remaining) is not None
    assert studio.refresh_user_pvm_class("Skid") == 1

    assert len(_members(studio, group)) == 1
    assert field_pipe in studio._pipe_items()
    assert field_pipe.data["a"] == removed_id
    assert field_pipe.data["a_source_element_id"] == removed_source
    assert any("unconnected pipe" in problem for problem in studio.validate())
    studio.close()


def test_class_master_save_refreshes_only_linked_open_drafts(tmp_path):
    _application()
    library = UserPvmLibrary(tmp_path)
    assert library.add("Card", _class_items()) is not None
    window = HmiStudioWindow(
        lambda: {}, tmp_path, area_name="Plant")
    display = window.current()
    if not display._holds_lock:
        display.enter_edit()
    assert display.place_user_pvm("Card", 50, 60, link="linked") == 3
    assert display.place_user_pvm("Card", 240, 60, link="unlinked") == 3
    linked = next(item for item in display._static_items()
                  if item.data.get("instance_definition") == "Card"
                  and item.data.get("instance_link") == "linked")
    unlinked = next(item for item in display._static_items()
                    if item.data.get("instance_definition") == "Card"
                    and item.data.get("instance_link") == "unlinked")
    linked_id = linked.data["id"]
    unlinked_snapshot = (unlinked.data["id"], unlinked.data.get("fill"),
                         unlinked.data["definition_revision"])

    display._sync_document()
    display.store.publish(display.display, findings=())
    published = (tmp_path / "Overview" / "revisions" / "1.json")
    published_before = published.read_bytes()

    master = window.edit_user_pvm_layout("Card")
    body = next(item for item in master._static_items()
                if item.data.get("id") == "body")
    body.data["fill"] = "#764C8A"
    master.mark_unsaved()
    assert master.save_draft()

    linked = next(item for item in display._static_items()
                  if item.data.get("instance_definition") == "Card"
                  and item.data.get("instance_link") == "linked"
                  and item.data.get("pvm_index") == 0)
    unlinked = next(item for item in display._static_items()
                    if item.data.get("instance_definition") == "Card"
                    and item.data.get("instance_link") == "unlinked")
    assert linked.data["id"] != linked_id
    assert linked.data["fill"] == "#764C8A"
    assert (unlinked.data["id"], unlinked.data.get("fill"),
            unlinked.data["definition_revision"]) == unlinked_snapshot
    assert display.display.work_in_progress
    assert "Card" in display.display.wip_reason
    assert published.read_bytes() == published_before
    window.close()


def test_closed_display_refreshes_linked_snapshot_on_enter_edit(tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Skid", _class_items()) is not None
    studio = _studio(tmp_path)
    assert studio.place_user_pvm("Skid", 100, 80, link="linked") == 3
    assert studio.place_user_pvm("Skid", 350, 80, link="unlinked") == 3
    linked = next(item for item in studio._static_items()
                  if item.data.get("instance_definition") == "Skid"
                  and item.data.get("instance_link") == "linked"
                  and item.data.get("pvm_index") == 0)
    linked_group = linked.data["group"]
    linked_source = linked.data["source_element_id"]
    linked_old_id = linked.data["id"]
    unlinked = next(item for item in studio._static_items()
                    if item.data.get("instance_definition") == "Skid"
                    and item.data.get("instance_link") == "unlinked")
    unlinked_source = unlinked.data["source_element_id"]
    unlinked_snapshot = (unlinked.data["id"], unlinked.data.get("fill"),
                         unlinked.data["definition_revision"])
    external = studio.add_static("rect", 250, 80, 30, 30)
    pipe = studio.add_pipe(linked, "e", external, "w")
    pipe_id = pipe.data["id"]
    assert studio.save_draft()
    studio._sync_document()
    studio.store.publish(studio.display, findings=())
    published = tmp_path / "Drawing" / "revisions" / "1.json"
    published_before = published.read_bytes()
    studio.close()

    rows = copy.deepcopy(UserPvmLibrary(tmp_path).entries["Skid"]["items"])
    next(row for row in rows if row.get("id") == "body")["fill"] = "#584080"
    assert UserPvmLibrary(tmp_path).add("Skid", rows) is not None

    reopened = PvmStudio(lambda: {}, tmp_path, display_name="Drawing")
    before_edit = next(item for item in _members(reopened, linked_group)
                       if item.data.get("source_element_id") == linked_source)
    assert before_edit.data["id"] == linked_old_id
    assert before_edit.data["fill"] != "#584080"
    assert published.read_bytes() == published_before

    reopened.enter_edit()
    refreshed = next(item for item in _members(reopened, linked_group)
                     if item.data.get("source_element_id") == linked_source)
    assert refreshed.data["id"] != linked_old_id
    assert refreshed.data["fill"] == "#584080"
    unlinked = next(item for item in reopened._static_items()
                    if item.data.get("instance_definition") == "Skid"
                    and item.data.get("instance_link") == "unlinked"
                    and item.data.get("source_element_id")
                    == unlinked_source)
    assert (unlinked.data["id"], unlinked.data.get("fill"),
            unlinked.data["definition_revision"]) == unlinked_snapshot
    same_pipe = next(item for item in reopened._pipe_items()
                     if item.data["id"] == pipe_id)
    assert same_pipe.data["a"] == refreshed.data["id"]
    assert reopened.display.work_in_progress
    assert reopened.unsaved
    assert published.read_bytes() == published_before
    reopened.close()


def test_legacy_index_override_tracks_source_through_delete_and_reorder(
        tmp_path):
    items = [
        {"kind": "rect", "id": "first", "x": 0, "y": 0,
         "w": 20, "h": 20, "fill": "#111111"},
        {"kind": "rect", "id": "middle", "x": 30, "y": 0,
         "w": 20, "h": 20, "fill": "#222222"},
        {"kind": "rect", "id": "target", "x": 60, "y": 0,
         "w": 20, "h": 20, "fill": "#333333"},
    ]
    library = UserPvmLibrary(tmp_path)
    assert library.add("Row", items) is not None
    studio = _studio(tmp_path)
    assert studio.place_user_pvm("Row", 100, 100) == 3
    target = next(item for item in studio._static_items()
                  if item.data.get("instance_definition") == "Row"
                  and item.data.get("pvm_index") == 2)
    group = target.data["group"]
    target_source = target.data["source_element_id"]
    # Simulate an older saved display. The placed member UUIDs are already
    # durable, but its override map still addresses the third array member.
    for member in _members(studio, group):
        member.data["instance_overrides"] = {"2.fill": "#C04040"}
        member.data["pvm_overrides"] = {"2.fill": "#C04040"}

    entry = UserPvmLibrary(tmp_path).entries["Row"]
    by_id = {row["id"]: copy.deepcopy(row) for row in entry["items"]}
    assert UserPvmLibrary(tmp_path).add(
        "Row", [by_id["target"], by_id["middle"]]) is not None
    assert studio.refresh_user_pvm_class("Row") == 1

    target = next(item for item in _members(studio, group)
                  if item.data.get("source_element_id") == target_source)
    middle = next(item for item in _members(studio, group)
                  if item.data.get("source_element_id") != target_source)
    canonical = UserPvmLibrary.override_key(target_source, "fill")
    assert target.data["pvm_index"] == 0
    assert target.data["fill"] == "#C04040"
    assert middle.data["fill"] == "#222222"
    assert target.data["instance_overrides"] == {canonical: "#C04040"}
    assert "2.fill" not in target.data["instance_overrides"]
    assert studio.remove_user_pvm_override(group, 0, "fill")
    target = next(item for item in _members(studio, group)
                  if item.data.get("source_element_id") == target_source)
    assert target.data["fill"] == "#333333"
    studio.close()


def test_detached_nested_child_is_frozen_across_parent_refresh(tmp_path):
    library = UserPvmLibrary(tmp_path)
    child_items = [
        {"kind": "rect", "id": "child-body", "x": 0, "y": 0,
         "w": 30, "h": 20, "fill": "#315B74"},
        {"kind": "text", "id": "child-label", "x": 35, "y": 0,
         "w": 60, "h": 20, "text": "FROZEN"},
        {"kind": "pipe", "id": "child-pipe", "a": "child-body",
         "a_side": "e", "b": "child-label", "b_side": "w"},
    ]
    assert library.add("Child", child_items) is not None
    assert library.add("Parent", [{
        "kind": "rect", "id": "parent-body", "x": 0, "y": 0,
        "w": 120, "h": 70, "fill": "#D0D5D8",
    }]) is not None
    assert library.add_nested("Parent", "Child", x=10, y=35)

    studio = _studio(tmp_path)
    assert studio.place_user_pvm(
        "Parent", 100, 80, unlink_nested=True) == 4
    child = [item for item in studio._static_items()
             if item.data.get("user_pvm") == "Child"]
    assert len(child) == 2
    group = child[0].data["group"]
    child_snapshot = {
        item.data["source_element_id"]: (
            item.data.get("kind"), item.data.get("fill"),
            item.data.get("text"), item.data.get("source_x"),
            item.data.get("source_y"))
        for item in child
    }
    child_body = next(item for item in child
                      if item.data.get("kind") == "rect")
    external = studio.add_static("rect", 300, 100, 20, 20)
    pipe = studio.add_pipe(child_body, "e", external, "w")
    pipe_id = pipe.data["id"]
    attached_source = child_body.data["source_element_id"]
    assert studio.save_draft()
    studio.close()

    revised_child = copy.deepcopy(
        UserPvmLibrary(tmp_path).entries["Child"]["items"])
    next(row for row in revised_child
         if row.get("id") == "child-body")["fill"] = "#D04040"
    revised_child.append({
        "kind": "text", "id": "new-child-member", "x": 0, "y": 25,
        "w": 80, "h": 20, "text": "NEW",
    })
    assert UserPvmLibrary(tmp_path).add("Child", revised_child) is not None
    revised_parent = copy.deepcopy(
        UserPvmLibrary(tmp_path).entries["Parent"]["items"])
    next(row for row in revised_parent
         if row.get("id") == "parent-body")["fill"] = "#A0A8AE"
    assert UserPvmLibrary(tmp_path).add("Parent", revised_parent) is not None
    studio = PvmStudio(lambda: {}, tmp_path, display_name="Drawing")
    studio.enter_edit()

    child = [item for item in _members(studio, group)
             if item.data.get("user_pvm") == "Child"]
    assert len(child) == 2
    assert {
        item.data["source_element_id"]: (
            item.data.get("kind"), item.data.get("fill"),
            item.data.get("text"), item.data.get("source_x"),
            item.data.get("source_y"))
        for item in child
    } == child_snapshot
    assert len([item for item in studio._pipe_items()
                if item.data.get("instance_internal")
                and item.data.get("instance_id")
                == child[0].data["instance_id"]]) == 1
    same_pipe = next(item for item in studio._pipe_items()
                     if item.data["id"] == pipe_id)
    refreshed_body = next(
        item for item in child
        if item.data["source_element_id"] == attached_source)
    assert same_pipe.data["a"] == refreshed_body.data["id"]
    parent = next(item for item in _members(studio, group)
                  if item.data.get("user_pvm") == "Parent")
    assert parent.data["fill"] == "#A0A8AE"
    studio.close()
