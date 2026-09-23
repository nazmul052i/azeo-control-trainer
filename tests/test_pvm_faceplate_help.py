"""The reusable-class tutorial stays complete and executable."""
from __future__ import annotations

import re
from pathlib import Path

from azeo_control_trainer.azeo_graphics_designer.configurator.designer import (
    PvmConfigDesigner,
)
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    OPERATOR_WRITE,
)
from azeo_control_trainer.azeo_graphics_designer.studio_help import TOPIC_BY_KEY


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "PVM_FACEPLATE_TUTORIAL.md"


def test_class_builder_help_covers_the_complete_release_workflow():
    topic = TOPIC_BY_KEY["class_builder_workflow"]
    searchable = topic.search_text
    for required in (
            "paired pvm", "class-master canvas", "public",
            "internal", "primary drop target", "table", "alarm list",
            "chart / trend", "user entry", "control data",
            "configure instance", "find usages", "publish each",
            "troubleshoot"):
        assert required in searchable


def test_illustrated_guide_has_nine_numbered_procedures_and_real_images():
    source = GUIDE.read_text(encoding="utf-8")
    headings = re.findall(r"^## ([1-9])\. (.+)$", source, re.MULTILINE)
    assert [number for number, _title in headings] == list("123456789")

    expected_titles = (
        "Create a PVM and faceplate pair",
        "Draw on the class-master canvas",
        "Define public and internal properties",
        "Configure the typed primary drop target",
        "Bind text, tables, alarms, trends, and entries",
        "Drag a control block onto a display",
        "Configure the placed instance",
        "Validate, Save, and publish affected displays",
        "Troubleshooting examples",
    )
    assert tuple(title for _number, title in headings) == expected_titles

    images = re.findall(r"!\[[^]]*\]\(([^)]+)\)", source)
    assert len(images) >= 12
    for relative in images:
        assert (GUIDE.parent / relative).is_file(), relative


def test_faceplate_blueprint_declares_a_checked_sp_write():
    config = PvmConfigDesigner._faceplate_configuration("TutorialLoop")
    assert config.property("ControlTag").drop_target
    assert config.property("SPPath").direction == OPERATOR_WRITE
    assert config.issues() == ()
