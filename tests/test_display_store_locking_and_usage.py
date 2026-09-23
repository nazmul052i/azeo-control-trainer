"""Single-writer ownership and authored-class deployment discovery."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from azeo_control_trainer.core.hmi.pvms import publishing
from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayLocked,
    DisplayStore,
    PvmDisplay,
    displays_using_class,
)


def test_same_username_in_a_second_session_is_refused_and_cannot_release(
        tmp_path):
    owner = DisplayStore(tmp_path)
    intruder = DisplayStore(tmp_path)
    owner_token = owner.acquire_lock("Overview", who="engineer")

    with pytest.raises(DisplayLocked, match="engineer"):
        intruder.acquire_lock("Overview", who="engineer")

    intruder.release_lock("Overview", who="engineer")
    lock = json.loads(
        (tmp_path / "Overview" / ".lock").read_text(encoding="utf-8"))
    assert lock["token"] == owner_token

    owner.release_lock("Overview", who="engineer")
    assert not (tmp_path / "Overview" / ".lock").exists()


def test_atomic_acquire_has_exactly_one_winner(tmp_path):
    barrier = threading.Barrier(2)

    def attempt():
        store = DisplayStore(tmp_path)
        barrier.wait(timeout=2)
        try:
            token = store.acquire_lock("Overview", who="engineer")
        except DisplayLocked:
            return "locked", store, ""
        return "owner", store, token

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: attempt(), range(2)))

    assert sorted(result[0] for result in results) == ["locked", "owner"]
    winner = next(result for result in results if result[0] == "owner")
    document = json.loads(
        (tmp_path / "Overview" / ".lock").read_text(encoding="utf-8"))
    assert document["token"] == winner[2]
    winner[1].release_lock("Overview")


def test_stale_lock_is_recovered_without_removing_unsaved_checkpoint(
        tmp_path):
    directory = tmp_path / "Overview"
    directory.mkdir(parents=True)
    recovery = directory / ".recovery.json"
    recovery.write_text(json.dumps({
        "display": "Overview", "pvms": [], "description": "unsaved",
        "level": 1,
    }), encoding="utf-8")
    (directory / ".lock").write_text(json.dumps({
        "who": "crashed-session",
        "at": time.time() - publishing._LOCK_STALE_S - 1,
        "token": "stale-token",
    }), encoding="utf-8")

    store = DisplayStore(tmp_path)
    token = store.acquire_lock("Overview", who="engineer")

    lock = json.loads((directory / ".lock").read_text(encoding="utf-8"))
    assert lock["token"] == token
    assert recovery.exists()
    assert store.load_recovery("Overview").description == "unsaved"
    store.release_lock("Overview")


def test_affected_display_discovery_prefers_root_identity_and_tracks_nested():
    documents = [
        PvmDisplay("Legacy", items=[{
            "user_pvm": "Parent", "pvm_link": "linked",
        }]),
        PvmDisplay("ModernLinked", items=[{
            "user_pvm": "Child", "pvm_link": "linked",
            "pvm_chain": ["Parent", "Child"],
            "instance_definition": "Parent",
            "instance_link": "linked",
        }]),
        PvmDisplay("NestedFrozen", items=[{
            "user_pvm": "Child", "pvm_link": "unlinked",
            "pvm_chain": ["Parent", "Child"],
            "instance_definition": "Parent",
            "instance_link": "linked",
        }]),
        PvmDisplay("RootFrozen", items=[{
            # Modern root metadata is authoritative over stale legacy fields.
            "user_pvm": "Parent", "pvm_link": "linked",
            "instance_definition": "Parent",
            "instance_link": "unlinked",
        }]),
        {
            "display": "NestedOnly",
            "pvms": [],
            "items": [{
                "user_pvm": "Leaf", "pvm_link": "linked",
                "pvm_chain": ["Parent", "Intermediate", "Leaf"],
                "instance_definition": "Parent",
                "instance_link": "linked",
            }],
        },
    ]

    assert displays_using_class("Parent", documents) == (
        "Legacy", "ModernLinked", "NestedFrozen", "NestedOnly",
    )
    assert displays_using_class("Child", documents) == ("ModernLinked",)
    assert displays_using_class("Intermediate", documents) == ("NestedOnly",)
    assert displays_using_class("Leaf", documents) == ("NestedOnly",)
