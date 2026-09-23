"""Check course structure and named source facts without launching or changing a plant."""
from __future__ import annotations

import json
from pathlib import Path
import re

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "docs" / "training"


def validate():
    manifest = json.loads((COURSE / "course_manifest.json").read_text(encoding="utf-8"))
    workbook = (COURSE / "LEARNER_WORKBOOK.md").read_text(encoding="utf-8")
    instructor = (COURSE / "INSTRUCTOR_GUIDE.md").read_text(encoding="utf-8")
    expected_labs = [f"L{i:02}" for i in range(1, 19)] + ["CAP01"]
    found_labs, total = [], 0
    for i, session in enumerate(manifest["sessions"], 1):
        assert session["id"] == f"C{i:02}", session
        assert session["day"] == (i + 1) // 2, session
        minutes = (session["teaching_minutes"] + session["review_minutes"]
                   + sum(lab["minutes"] for lab in session["labs"]))
        assert minutes == 240, session
        total += minutes
        for lab in session["labs"]:
            lab_id = lab["id"]
            found_labs.append(lab_id)
            assert re.search(rf"(?m)^### {lab_id} - ", workbook), lab_id
            assert re.search(rf"\b{lab_id}\b", instructor), lab_id
    assert found_labs == expected_labs, found_labs
    assert total == manifest["contact_minutes"] == 2400
    assert re.findall(r"(?m)^Q(\d{2})\.", workbook) == [f"{i:02}" for i in range(1, 21)]
    assert re.findall(r"(?m)^\| Q(\d{2}) \|", instructor) == [f"{i:02}" for i in range(1, 21)]
    assessment = manifest["assessment"]
    assert sum(assessment[k] for k in ("portfolio", "capstone", "knowledge")) == 100
    assert assessment["pass_score"] == 80 and assessment["critical_competencies_required"]
    facts = 0
    for contract in manifest["source_contracts"]:
        source = json.loads((ROOT / contract["file"]).read_text(encoding="utf-8"))
        blocks = {b["instance_name"]: b for b in source["blocks"]}
        for name, kind in contract["blocks"].items():
            assert blocks[name]["block_type"] == kind, (contract["file"], name, kind)
            facts += 1
        for name, parameters in contract.get("config", {}).items():
            for parameter, value in parameters.items():
                actual = blocks[name]["config"][parameter]
                assert actual == value, (contract["file"], name, parameter, actual, value)
                facts += 1
    for path in manifest["source_documents"]:
        assert (ROOT / path).is_file(), path
    parser = MarkdownIt("commonmark")
    links = 0
    for source in COURSE.glob("*.md"):
        content = source.read_text(encoding="utf-8")
        assert not any(char in content for char in "\u2010\u2011\u2012\u2013\u2014\u2212"), source
        for token in parser.parse(content):
            for child in token.children or []:
                if child.type != "link_open":
                    continue
                href = child.attrGet("href")
                if href.startswith(("http://", "https://", "#")):
                    continue
                target = source.parent / href.split("#", 1)[0]
                assert target.is_file(), (str(source), href)
                links += 1
    return {"course": manifest["course_id"], "contact_hours": total / 60,
            "guided_labs": 18, "capstones": 1, "questions_and_answers": 20,
            "source_facts_checked": facts, "local_links_checked": links,
            "dynamic_lab_qualification": "Requires instructor execution and QF1 signoff"}


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
