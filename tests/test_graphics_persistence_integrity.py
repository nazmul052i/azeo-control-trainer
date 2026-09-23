"""Crash safety and schema evolution for Graphics Designer documents."""
from __future__ import annotations

import json
import os

import pytest

from azeo_control_trainer.core.hmi.pvms import json_io
from azeo_control_trainer.core.hmi.pvms.json_io import atomic_write_json
from azeo_control_trainer.core.hmi.pvms.publishing import (
    CURRENT_DISPLAY_SCHEMA_VERSION,
    DisplayStore,
    PvmDisplay,
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_atomic_json_failure_preserves_previous_complete_document(
        tmp_path, monkeypatch):
    destination = tmp_path / "draft.json"
    atomic_write_json(destination, {"revision": "previous"})

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(json_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        atomic_write_json(destination, {"revision": "partial"})

    assert _read(destination) == {"revision": "previous"}
    assert list(tmp_path.glob(".draft.json.*.tmp")) == []


def test_legacy_schema_is_read_without_rewrite_and_upgraded_on_save(tmp_path):
    path = tmp_path / "Overview" / "draft.json"
    path.parent.mkdir(parents=True)
    legacy = {"display": "Overview", "pvms": [], "level": 2}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    before = path.read_bytes()

    store = DisplayStore(tmp_path)
    display = store.load_draft("Overview")

    assert display is not None
    assert display.schema_version is None
    assert path.read_bytes() == before
    assert "schema_version" not in display.to_dict()

    store.save_draft(display)
    saved = _read(path)
    assert saved["schema_version"] == CURRENT_DISPLAY_SCHEMA_VERSION
    assert display.schema_version == CURRENT_DISPLAY_SCHEMA_VERSION


def test_same_timestamp_recovery_uses_base_draft_identity(tmp_path):
    store = DisplayStore(tmp_path)
    display = PvmDisplay("Overview", description="saved")
    draft_path = store.save_draft(display)

    display.description = "unsaved recovery"
    recovery_path = store.save_recovery(display)
    stamp = max(draft_path.stat().st_mtime_ns,
                recovery_path.stat().st_mtime_ns)
    os.utime(draft_path, ns=(stamp, stamp))
    os.utime(recovery_path, ns=(stamp, stamp))

    recovered = store.load_recovery("Overview")
    assert recovered is not None
    assert recovered.description == "unsaved recovery"

    # An explicit later Save wins even when a coarse filesystem reports the
    # exact same timestamp for the old recovery checkpoint.
    display.description = "explicit later save"
    store.save_draft(display)
    os.utime(draft_path, ns=(stamp, stamp))
    os.utime(recovery_path, ns=(stamp, stamp))
    assert store.load_recovery("Overview") is None


def test_legacy_same_timestamp_recovery_remains_readable(tmp_path):
    directory = tmp_path / "Overview"
    directory.mkdir(parents=True)
    draft = directory / "draft.json"
    recovery = directory / ".recovery.json"
    draft.write_text(json.dumps({
        "display": "Overview", "pvms": [], "description": "saved",
        "level": 2,
    }), encoding="utf-8")
    recovery.write_text(json.dumps({
        "display": "Overview", "pvms": [], "description": "unsaved",
        "level": 2,
    }), encoding="utf-8")
    stamp = max(draft.stat().st_mtime_ns, recovery.stat().st_mtime_ns)
    os.utime(draft, ns=(stamp, stamp))
    os.utime(recovery, ns=(stamp, stamp))

    restored = DisplayStore(tmp_path).load_recovery("Overview")
    assert restored is not None
    assert restored.description == "unsaved"


def test_published_wip_revision_is_runtime_clean_and_schema_versioned(
        tmp_path):
    store = DisplayStore(tmp_path)
    display = PvmDisplay(
        "Overview", work_in_progress=True,
        wip_reason="waiting for P&ID approval",
    )

    entry = store.publish(display, allow_wip=True, by="engineer")
    revision = store.revision_document("Overview", entry["rev"])

    assert entry["published_as_wip"] is True
    assert revision is not None
    assert revision["schema_version"] == CURRENT_DISPLAY_SCHEMA_VERSION
    assert "work_in_progress" not in revision
    assert "wip_reason" not in revision
    assert not display.work_in_progress
    assert display.wip_reason == ""
    assert display.schema_version == CURRENT_DISPLAY_SCHEMA_VERSION


def test_failed_history_commit_does_not_clear_wip_draft(tmp_path, monkeypatch):
    store = DisplayStore(tmp_path)
    display = PvmDisplay(
        "Overview", work_in_progress=True, wip_reason="still under review",
    )

    def fail_history(_name, _entries):
        raise OSError("simulated history failure")

    monkeypatch.setattr(store, "_write_history", fail_history)
    with pytest.raises(OSError, match="history failure"):
        store.publish(display, allow_wip=True)

    assert display.work_in_progress
    assert display.wip_reason == "still under review"
    assert display.schema_version is None
    assert store.history("Overview") == []
