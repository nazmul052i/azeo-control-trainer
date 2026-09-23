"""Build the six offline product guides from the maintained suite manuals.

The section numbering intentionally follows USER_MANUAL.md. A procedure cited
from another Azeo application therefore has the same number in every guide.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SUITE = DOCS / "USER_MANUAL.md"
PA = DOCS / "PA_DESIGNER.md"
PRODUCT_EDITION = "Edition 1.0 - 23 September 2026"


@dataclass(frozen=True)
class ProductGuide:
    title: str
    filename: str
    document_id: str
    chapters: tuple[int, ...]
    purpose: str
    first_task: str
    second_task: str
    third_task: str


GUIDES = (
    ProductGuide("Azeo Explorer", "EXPLORER_HELP.md", "AZEO-UM-EXPLORER",
                 (2, 3, 16, 17),
                 "Project navigation, administration, shared configuration and recovery.",
                 "Open a project and identify its controller, units and modules",
                 "Inspect configured and running state before deployment",
                 "Back up, restore and investigate project or startup problems"),
    ProductGuide("Azeo Control Designer", "CONTROL_DESIGNER_HELP.md", "AZEO-UM-CONTROL",
                 (2, 4, 5, 17),
                 "Function-block module engineering, class reuse, download and live diagnosis.",
                 "Create, wire, compile and save a module",
                 "Review a Control Module Class and adopt an update",
                 "Download, monitor and safely return a module offline"),
    ProductGuide("Azeo Graphics Designer", "GRAPHICS_DESIGNER_HELP.md", "AZEO-UM-GRAPHICS",
                 (2, 6, 7, 8, 9, 17),
                 "Display drawing, PVM and faceplate authoring, verification and publication.",
                 "Create a blank or template-based display",
                 "Build and bind a reusable PVM and faceplate",
                 "Verify, commission, review and publish a revision"),
    ProductGuide("Azeo Operator Station", "OPERATOR_STATION_HELP.md", "AZEO-UM-STATION",
                 (2, 10, 11, 12, 17),
                 "Published-display operation, checked commands, alarms and process history.",
                 "Navigate live displays and open a faceplate",
                 "Investigate and acknowledge an alarm",
                 "Compare loop history and export evidence"),
    ProductGuide("Azeo Simulation Workbench", "SIMULATION_WORKBENCH_HELP.md", "AZEO-UM-SIMULATION",
                 (2, 13, 14, 15, 16),
                 "Simulation controls, snapshots, playback and instructor exercises.",
                 "Set up and step a training simulation",
                 "Save a starting snapshot and restore selected content",
                 "Run an exercise and review the session timeline"),
    ProductGuide("Azeo PA Designer", "PA_DESIGNER_HELP.md", "AZEO-UM-PA",
                 (2,),
                 "Procedure authoring, governed revision review and supervised operation.",
                 "Create and validate an advisory workflow",
                 "Map tags and review findings before saving a revision",
                 "Supervise a run and inspect its audit evidence"),
)


def suite_chapters(body: str) -> dict[int, str]:
    headings = list(re.finditer(r"(?m)^## (\d+)\. ", body))
    return {int(match.group(1)): body[match.start():headings[index + 1].start() if index + 1 < len(headings) else len(body)].rstrip()
            for index, match in enumerate(headings)}


def edition(body: str) -> tuple[str, str]:
    match = re.search(r"(?m)^Edition ([0-9.]+) - ([^\n\\]+)", body)
    baseline = re.search(r"(?m)^Application baseline: `([^`]+)`", body)
    if not match or not baseline:
        raise ValueError("The suite manual needs its edition, date and baseline header")
    return f"Edition {match[1]} - {match[2]}", baseline[1]


def pa_chapters(body: str) -> str:
    body = re.sub(r"\A# [^\n]+\n+", "", body).strip()
    figures = {
        "## Build a procedure": "![PA-1. New procedure provides blank and tested starting structures.](images/user_manual/pa-designer-new-procedure.png)\n\n",
        "## Engineering productivity workflows": "![PA-2. The actual workflow editor shows the library, selected instruction and inspector in a disposable APVC copy. The Problems count reflects this isolated capture, not release readiness.](images/user_manual/pa-designer-workflow.png)\n\n",
        "## Visual workflow": "![PA-3. Tag mappings connect logical procedure names to configured project parameters.](images/user_manual/pa-designer-tag-mappings.png)\n\n",
    }
    for heading, figure in figures.items():
        if heading not in body:
            raise ValueError(f"Missing PA section: {heading}")
        body = body.replace(heading + "\n", heading + "\n\n" + figure, 1)
    return body


def render(guide: ProductGuide, chapters: dict[int, str], pa: str, header: str, baseline: str) -> str:
    sections = [chapters[number] for number in guide.chapters]
    if guide.filename == "PA_DESIGNER_HELP.md":
        sections.append(pa_chapters(pa))
    introduction = f"""# {guide.title} User Manual

{header}
Application baseline: `{baseline}`
Document ID: {guide.document_id}

{guide.purpose} This guide uses the current Azeo application names and real
widget captures. Examples use the training system; values and demonstrated
conditions are not operating targets for a real plant.

Section numbers inherited from the [suite user manual](USER_MANUAL.md) are kept
intact so cross-application references remain accurate. Application-specific
commands depend on the active project, selected object and granted authority.

## Start with a task

| Goal | Where to go |
| --- | --- |
| {guide.first_task} | Relevant numbered section below |
| {guide.second_task} | Relevant numbered section below |
| {guide.third_task} | Relevant numbered section below |

**Before changing live state:** use an instructor-approved training copy,
check the selected project and current source quality, and keep Save, Download,
Publish, Refresh and operator commands distinct. The [installation guide](INSTALLATION_GUIDE.md)
covers setup and repair. **Help > System information** gives the actual build,
licence and support paths on the workstation.

"""
    ending = """
### Related Azeo help

- [Complete suite user manual](USER_MANUAL.md) - connected workflow and glossary.
- [Installation and administration](INSTALLATION_GUIDE.md) - setup, update and repair.
- [PA Designer authoring guide](PA_DESIGNER.md) - block and procedure details.
- [Control Module Class tutorial](CONTROL_MODULE_CLASS_TUTORIAL.md) - reusable control engineering.
- [PVM and faceplate tutorial](PVM_FACEPLATE_TUTORIAL.md) - reusable graphics engineering.

Use the application's F1 help for the installed block or selected tool. A screenshot
documents the interface, not live readiness or permission to perform a command.
"""
    return introduction + "\n\n".join(sections) + "\n" + ending


def main() -> int:
    suite = SUITE.read_text(encoding="utf-8")
    chapter_map = suite_chapters(suite)
    pa = PA.read_text(encoding="utf-8")
    _suite_header, baseline = edition(suite)
    for guide in GUIDES:
        target = DOCS / guide.filename
        content = render(guide, chapter_map, pa, PRODUCT_EDITION, baseline)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")
        print(f"{target.relative_to(ROOT)}: {len(content.split())} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
