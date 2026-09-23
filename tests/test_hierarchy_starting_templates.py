"""The protected starting documents express four different operator tasks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import hierarchy_document


def test_templates_inherit_station_theme_and_have_no_invented_healthy_data():
    for level in range(1, 5):
        document = hierarchy_document(level, f"Level {level}")
        assert document.background == ""
        assert (document.width, document.height) == (1600, 900)
        for item in document.items:
            assert not any(item.get(key, "").startswith("#") for key in ("fill", "line", "text_color"))
            for row in item.get("rows", []):
                assert not any(str(v).lower() in ("normal", "true", "false", "ready", "ok") for v in row.values())
        assert any("configure" in str(i.get("text", "")).lower() for i in document.items)


def test_l1_is_read_only_kpis_and_l2_l3_have_real_configurable_pvms():
    first = hierarchy_document(1, "Overview")
    assert len(first.pvms) >= 8
    assert all(g.block_type == "AI" for g in first.pvms)
    for level in (2, 3):
        document = hierarchy_document(level, "Operation")
        assert any(g.block_type == "PID" for g in document.pvms)
        ids = {g.id for g in document.pvms} | {i["id"] for i in document.items}
        for item in document.items:
            if item["kind"] == "pipe":
                assert item["a"] in ids and item["b"] in ids
                assert item["a_side"] and item["b_side"]


def test_support_template_does_not_present_an_unbound_permit_as_false():
    document = hierarchy_document(4, "Support")
    tables = [i for i in document.items if i["kind"] == "table"]
    assert tables
    assert all(row.get("state") == "Not configured" for table in tables for row in table["rows"])
