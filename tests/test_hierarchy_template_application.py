"""Applying the ISA-101 templates to a project keeps every display ON the template.

The APVC hierarchy spec must build without touching template geometry, the
parity check must catch a hand-moved item or a swapped PVM class, and the
binding check must refuse a slot bound to the wrong block type.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SPEC = ROOT / "projects" / "AzeoPlantVirtualController" / "engineering" / "display_hierarchy.json"


@pytest.fixture(scope="module")
def tool():
    location = ROOT / "tools" / "apply_hierarchy_templates.py"
    spec = importlib.util.spec_from_file_location("apply_hierarchy_templates", location)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def apvc_spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_every_apvc_display_is_its_level_template_with_content_only(tool, apvc_spec):
    names = set(apvc_spec["displays"])
    for name, entry in apvc_spec["displays"].items():
        document = tool.build(name, entry)
        assert document.level == entry["level"] and document.parent == entry["parent"]
        assert tool.template_differences(document, name, entry) == []
        assert (document.width, document.height, document.show_tag) == (1600, 900, "none")
        # Slots are filled with real module paths; the template placeholders are gone.
        assert all(not str(g.params["path"]).startswith("CONFIGURE/") for g in document.pvms)
        assert all("CONFIGURE/" not in path for _ident, path in tool._paths(document))
        assert not any("TEMPLATE -" in str(item.get("text", "")) for item in document.items)
        for item in document.items:
            if item.get("kind") == "display_link":
                assert item["target"] in names
    levels = sorted({entry["level"] for entry in apvc_spec["displays"].values()})
    assert levels == [1, 2, 3, 4]


def test_the_spec_cannot_reach_past_the_slots(tool, apvc_spec):
    name = "U300 - L2 Charge Heating"
    entry = json.loads(json.dumps(apvc_spec["displays"][name]))
    entry["texts"]["no_such_item"] = "x"
    with pytest.raises(tool.SpecError, match="no item"):
        tool.build(name, entry)
    entry = json.loads(json.dumps(apvc_spec["displays"][name]))
    entry["pens"]["feed_tag"] = [["Feed", "FIC-3001/FIC-3001/PV"]]
    with pytest.raises(tool.SpecError, match="not a chart"):
        tool.build(name, entry)
    entry = json.loads(json.dumps(apvc_spec["displays"][name]))
    entry["pvms"]["loop_LEVEL"] = {"path": "PIC-3001/PIC-3001/PV"}
    with pytest.raises(tool.SpecError, match="MODULE/BLOCK"):
        tool.build(name, entry)


def test_parity_catches_moved_items_and_swapped_pvm_classes(tool, apvc_spec):
    name = "U100 - L2 Feed Preparation"
    entry = apvc_spec["displays"][name]
    document = tool.build(name, entry)
    document.items[5]["x"] += 4
    problems = tool.template_differences(document, name, entry)
    assert problems and "template geometry" in problems[0]
    document = tool.build(name, entry)
    document.pvms[0] = replace(document.pvms[0], variant="hp")
    assert any("variant" in problem for problem in tool.template_differences(document, name, entry))
    document = tool.build(name, entry)
    document.items.pop()
    assert any("template has" in problem for problem in tool.template_differences(document, name, entry))


def test_binding_check_refuses_wrong_block_types_and_unknown_terminals(tool, apvc_spec):
    name = "U100 - L2 Feed Preparation"
    document = tool.build(name, apvc_spec["displays"][name])
    blocks = {("FIC-1002", "FIC-1002"): "PID", ("PIC-1001", "PIC-1001"): "PID", ("LIC-1001", "LIC-1001"): "PID",
              ("PIC-1001", "PT-1002"): "AI", ("FIC-1001", "FT-1001"): "AI", ("SIC-1001", "ST-1001"): "AI",
              ("FIC-1001", "FIC-1001"): "PID"}
    terminals = {f"{module}/{block}/PV" for module, block in blocks}
    displays = set(apvc_spec["displays"])
    assert tool.binding_problems(document, blocks, terminals, displays) == []
    blocks[("FIC-1002", "FIC-1002")] = "AI"          # a PID slot bound to an analog input
    problems = tool.binding_problems(document, blocks, terminals, displays)
    assert any("slot needs PID" in problem for problem in problems)
    terminals.discard("FIC-1002/FIC-1002/PV")
    problems = tool.binding_problems(document, blocks, terminals, displays)
    assert any("no terminal" in problem for problem in problems)
    assert any("unknown display" in problem
               for problem in tool.binding_problems(document, blocks, terminals, set()))
