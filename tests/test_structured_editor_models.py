from azeo_control_trainer.core.hmi.pvms.elements import Action, UserEntry
from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
from azeo_control_trainer.azeo_graphics_designer.studio.editor_models import (
    action_issues,
    data_binding_issues,
    data_issues,
    merge_actions,
    merge_data_payload,
    property_descriptor_issues,
    user_entry_issues,
)


def test_data_merge_replaces_known_keys_and_preserves_extensions():
    original = {
        "kind": "chart",
        "pens": [{"path": "OLD/PV"}],
        "window_seconds": 30,
        "vendor_extension": {"keep": True},
    }
    candidate = merge_data_payload(original, {"pens": [{"path": "M/B/PV"}]})
    assert candidate["pens"] == [{"path": "M/B/PV"}]
    assert "window_seconds" not in candidate
    assert candidate["vendor_extension"] == {"keep": True}
    assert original["pens"] == [{"path": "OLD/PV"}]


def test_data_validation_rejects_bad_range_and_row_shape():
    issues = data_issues(
        {
            "kind": "chart",
            "pens": ["not-an-object"],
            "lo": 10,
            "hi": 5,
            "window_seconds": 0,
        }
    )
    fields = {issue.field for issue in issues}
    assert "pens[0]" in fields
    assert "range" in fields
    assert "window_seconds" in fields


def test_alarm_validation_is_no_longer_unbounded():
    issues = data_issues({"kind": "alarm_list", "priority_min": -1, "max_rows": 0})
    assert {issue.field for issue in issues} == {"priority_min", "max_rows"}


def test_action_validation_reports_dead_and_operator_unsupported_actions():
    drag = action_issues(Action(event="drag", kind="open_display", target="Overview"))
    assert any("not dispatched" in issue.message for issue in drag)
    studio_only = action_issues(Action(kind="show_tooltip", target="Help"))
    assert any("Studio-only" in issue.message for issue in studio_only)


def test_action_merge_preserves_unknown_extensions():
    rows = merge_actions(
        [{"event": "click", "kind": "open_display", "target": "Old", "audit_id": "A-1"}],
        [Action(kind="open_display", target="New")],
    )
    assert rows == [{"audit_id": "A-1", "event": "click", "kind": "open_display", "target": "New"}]


def test_user_entry_options_are_typed_and_unique():
    issues = user_entry_issues(
        UserEntry(kind="combo_box", path="M/B/MODE", options=((1, "On"), (1, "Run")))
    )
    assert any("unique" in issue.message for issue in issues)


def test_action_only_button_is_valid_and_dispatches_without_a_write_path():
    issues = user_entry_issues(UserEntry(kind="button", path=""))
    assert issues == ()

    seen = []
    item = StaticItem(
        {
            "kind": "user_entry",
            "entry": {"kind": "button", "label": "Open overview"},
            "actions": [
                {"event": "click", "kind": "open_display", "target": "Overview"}
            ],
        },
        {},
    )
    item.interaction_handler = lambda action, _item: seen.append(action) or True
    assert item.refresh_write_permission()
    assert item.activate()
    assert seen == [
        {"event": "click", "kind": "open_display", "target": "Overview"}
    ]


def test_property_descriptor_validation_reaches_expression_references():
    issues = property_descriptor_issues(
        {
            "kind": "expression",
            "expr": "pv - sp",
            "refs": {"pv": "M/B/PV", "sp": "M/B/MISSING"},
        },
        field="props.fill_pct",
        resolver=lambda path: path != "M/B/MISSING",
    )
    assert [(issue.field, issue.message) for issue in issues] == [
        ("props.fill_pct.refs.sp", "unresolved path M/B/MISSING")
    ]


def test_data_binding_validation_exposes_every_compound_path():
    issues = data_binding_issues(
        {
            "kind": "table",
            "columns": [{"key": "pv"}],
            "rows": [
                {"pv": {"path": "M/B/PV"}},
                {"pv": {"path": "M/B/MISSING"}},
            ],
        },
        resolver=lambda path: path.endswith("/PV"),
    )
    assert len(issues) == 1
    assert "M/B/MISSING" in issues[0].message
