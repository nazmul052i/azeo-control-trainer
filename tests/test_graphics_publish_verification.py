"""The same structured verifier gates Verify and Publish."""
from __future__ import annotations

import pytest

from azeo_control_trainer.core.hmi.pvms.publishing import (
    ERROR, WARNING, DisplayStore, Finding, PvmDisplay, PublishRefused,
    display_diff, displays_using_class,
)
from azeo_control_trainer.azeo_graphics_designer.studio.verification import (
    findings_for_studio,
)


def test_publish_refuses_error_findings_before_creating_history(tmp_path):
    store = DisplayStore(tmp_path)
    display = PvmDisplay("Overview")

    with pytest.raises(PublishRefused, match="unconnected pipe"):
        store.publish(display, findings=(
            Finding(ERROR, "unconnected pipe: pipe-7", "pipe-7"),
        ))

    assert store.history("Overview") == []
    assert store.revision_document("Overview", 1) is None


def test_warning_findings_are_visible_but_do_not_block_release(tmp_path):
    store = DisplayStore(tmp_path)
    entry = store.publish(PvmDisplay("Overview"), findings=(
        Finding(WARNING, "outside display page: label-2", "label-2"),
    ))

    assert entry["rev"] == 1
    assert store.revision_document("Overview", 1) is not None


def test_publish_diff_describes_native_drawing_and_page_changes():
    old = {
        "display": "Overview", "pvms": [], "width": 800, "level": 1,
        "items": [
            {"id": "v1", "kind": "symbol", "x": 10, "y": 20,
             "w": 80, "h": 100, "fill": "#AAA"},
            {"id": "old", "kind": "text", "text": "Remove"},
        ],
    }
    new = {
        "display": "Overview", "pvms": [], "width": 1200, "level": 2,
        "items": [
            {"id": "v1", "kind": "symbol", "x": 30, "y": 20,
             "w": 120, "h": 100, "fill": "#BBB",
             "props": {"fill": {"kind": "animation", "path": "M/B/P"}},
             "actions": [{"event": "click", "kind": "open_display",
                          "target": "Detail"}]},
            {"id": "new", "kind": "pipe", "a": "v1", "b": "v2"},
        ],
    }

    lines = display_diff(old, new)
    assert "+ pipe new" in lines
    assert "- text old" in lines
    assert "~ moved v1" in lines
    assert "~ resized v1" in lines
    assert "~ restyled v1" in lines
    assert "~ rebound/configured v1" in lines
    assert "~ interaction changed v1" in lines
    assert "~ page width changed" in lines
    assert "~ display level changed" in lines


def test_publish_diff_names_pvm_fill_and_line_changes():
    old = {"display": "Overview", "pvms": [{
        "id": "loop", "class": "PID/dynamo_compact",
        "params": {"path": "UNIT/PID1"}, "x": 10, "y": 10,
        "w": 176, "h": 94,
    }]}
    new = {"display": "Overview", "pvms": [{
        **old["pvms"][0], "fill": "#d8e0e7", "line": "#425b70",
    }]}

    assert display_diff(old, new) == [
        "~ appearance UNIT/PID1 fill=#d8e0e7 line=#425b70"]


def test_affected_display_discovery_includes_only_linked_authored_instances():
    documents = [
        PvmDisplay("Linked", items=[
            {"id": "a", "user_pvm": "PumpSkid", "pvm_link": "linked"},
        ]),
        PvmDisplay("Unlinked", items=[
            {"id": "b", "user_pvm": "PumpSkid", "pvm_link": "unlinked"},
        ]),
    ]
    assert displays_using_class("PumpSkid", documents) == ("Linked",)


def test_direct_studio_publish_runs_the_complete_verifier(tmp_path):
    findings = (Finding(ERROR, "unconnected pipe: p1", "p1"),)

    class Studio:
        display = PvmDisplay("Overview")
        store = DisplayStore(tmp_path)
        _draft_fingerprint = ""

        def verification_findings(self):
            return findings

        def _sync_document(self):
            return None

        def _resolve(self, path):
            return True

        def save_draft(self):
            return True

    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    Studio.store.acquire_lock("Overview")
    with pytest.raises(PublishRefused, match="unconnected pipe"):
        PvmStudio.publish(Studio())
    assert Studio.store.history("Overview") == []


def test_studio_publish_does_not_accept_a_verifier_bypass(tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio

    class Studio:
        display = PvmDisplay("Overview")
        store = DisplayStore(tmp_path)

        def verification_findings(self):
            return (Finding(ERROR, "bad action: button-1", "button-1"),)

        def _sync_document(self):
            return None

        def _resolve(self, path):
            return True

    with pytest.raises(TypeError, match="findings"):
        PvmStudio.publish(Studio(), findings=())
    assert Studio.store.history("Overview") == []


def test_verify_and_publish_share_the_same_structured_finding_set():
    class Issue:
        severity = ERROR
        location = "interface"

        def __str__(self):
            return "required public property missing"

    class Config:
        def issues(self):
            return (Issue(),)

    class Studio:
        def validate(self):
            return ["unconnected pipe: p1",
                    "outside display page: note"]

        def edited_user_class_name(self):
            return "ValveClass"

        def _config_for_name(self, name):
            return Config()

    findings = findings_for_studio(Studio())
    assert [(one.severity, one.message) for one in findings] == [
        (ERROR, "required public property missing"),
        (ERROR, "unconnected pipe: p1"),
        (WARNING, "outside display page: note"),
    ]


def test_verifier_covers_property_refs_actions_and_duplicate_ids(tmp_path):
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED

    DisplayStore(tmp_path).save_draft(PvmDisplay("Overview"))

    class Source:
        def read(self, path):
            return UNRESOLVED

    class Engine:
        _source = Source()

    class Item:
        def __init__(self, data):
            self.data = data

    items = [
        Item({
            "id": "button-1", "kind": "rect",
            "props": {"fill_pct": {
                "kind": "expression", "expr": "pv * 2",
                "refs": {"pv": "UNIT/PID/PV"},
            }},
            "actions": [{"event": "click", "kind": "open_display",
                         "target": "Missing"}],
        }),
        Item({"id": "button-1", "kind": "text", "text": "duplicate"}),
    ]

    class Studio:
        store = DisplayStore(tmp_path)
        display = PvmDisplay("Overview")
        engine = Engine()

        def validate(self):
            return []

        def edited_user_class_name(self):
            return ""

        def _static_items(self):
            return items

        def _items(self):
            return []

        def _pipe_items(self):
            return []

    findings = findings_for_studio(Studio())
    messages = {finding.message for finding in findings}
    assert "unresolved path UNIT/PID/PV" in messages
    assert "display does not exist" in messages
    assert "duplicate object id: button-1" in messages


def test_problems_pane_emits_structured_object_identity():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_graphics_designer.studio.problems import ProblemsPane

    QApplication.instance() or QApplication([])
    pane = ProblemsPane()
    pane.set_problems("Overview", (
        Finding(ERROR, "unconnected pipe: p1", "p1"),
    ))
    row = pane.topLevelItem(0)
    assert row.text(2) == "p1"
    assert row.data(0, Qt.UserRole) == "p1"


def test_class_master_held_binding_is_not_reported_as_empty_path():
    from types import SimpleNamespace

    class Config:
        def issues(self):
            return ()

    item = SimpleNamespace(data={
        "id": "readout", "source_element_id": "source-1",
        "kind": "datalink", "path": "",
    })
    library = SimpleNamespace(entries={"LoopCard": {"items": [{
        "id": "readout", "source_element_id": "source-1",
        "kind": "datalink", "path": "Pvm.ControlTag",
    }]}})

    class Studio:
        def validate(self):
            return ["Data Link: no data source"]

        def edited_user_class_name(self):
            return "LoopCard"

        def _config_for_name(self, name):
            return Config()

        def user_library(self):
            return library

        def _static_items(self):
            return [item]

        def _items(self):
            return []

        def _pipe_items(self):
            return []

    assert findings_for_studio(Studio()) == ()
