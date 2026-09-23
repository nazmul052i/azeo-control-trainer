#!/usr/bin/env python
"""Run Azeo Control Trainer straight from the source tree.

No install, no ``PYTHONPATH`` — this puts ``src/`` on the path itself, so a
fresh clone runs with:

    D:\\development\\GitHub\\vpy\\Scripts\\python.exe run.py

``run.py --help`` prints the full command list. Once ``pip install -e .`` has
been run, the ``azeo`` console script does the same thing and this file is
redundant.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PY = sys.executable

HELP = f"""\
Azeo Control Trainer -- an IEC-61131 engineering environment with a DCS
operator station over it.

USAGE
  {PY} run.py [AREA] [options]
  {PY} run.py <command>

OPENING THE TRAINER
  run.py                     boot Azeo Explorer over the registered default project
  run.py --classic           Control Designer first, no Explorer shell
  run.py --graphics          Graphics Designer first
  run.py --procedures        PA Designer authoring, without starting a runtime
  run.py --simulation        Simulation Workbench first
  run.py --simulator         focused plant simulator: process, disturbances and I/O
  run.py <area>              a named area under src/strategies
  run.py <path>              an area anywhere on disk
  run.py --blank             an empty scratch area
  run.py --online            download the area on startup, so it opens running
  run.py --station           dedicated Azeo-style operator runtime; Control
                             Studio remains hidden until a faceplate's
                             engineering action opens its associated module
  run.py --no-plant          engineering only: nothing drives the tags, so
                             every input reports Bad -- which is what a
                             controller with no I/O should say (DECISIONS D2)

INSPECTING WITHOUT THE UI
  run.py --list              the strategy areas available, then exit
  run.py --tagdb [FILE]      the derived tag database; writes CSV/JSON if a
                             file is given, otherwise prints the field tags a
                             plant must supply
  run.py --plants            the simulated processes this build ships
  run.py --check             verify a plant's tag contract against the modules
  run.py --displays          the operator displays an area ships
  run.py --vision            style-guide vision test: colour-blind and
                             greyscale conformance of the alarm palette
  run.py --upsets            process upsets a scenario can inject

THE TRAINING LOOP, ONCE IT IS OPEN
  1  Control Designer opens with the area's modules, one tab each.
  2  Download (F5) puts them on scan. The executive starts scanning at
     500 ms; wire values and the status bar come alive.
  3  View > Operator Station  (Ctrl+Shift+O) opens the console:
     alarm banner, process graphic, faceplates, trends.
  4  On the graphic, click a symbol to open its faceplate. Start MTR-102
     from there -- it will refuse until XV-101 is open and T-101 is above
     its low-level limit, and it will tell you which.
  5  Trend  opens Process History View for the selected module.
     Tag Database (Ctrl+T) browses every addressable point.

OTHER WINDOWS
  Ctrl+Shift+O   Operator Station        Ctrl+T   Tag Database
  Ctrl+M         Monitoring tab          Ctrl+Shift+W   Watch window
  F7 compile | F5 download | Shift+F5 go off line

TESTS
  {PY} tests\\_smoke_trainer.py      the fork's contract test
  {PY} tests\\_smoke_tagdb.py        tag database
  {PY} tests\\_smoke_operator.py     faceplates, scan executive, tag browser
  {PY} tests\\_smoke_hmi_core.py     shared symbols, history and HMI standards
  {PY} tests\\_smoke_plant_hmi.py    plant -> controller -> alarms -> screen

DOCS
  CLAUDE.md              conventions, and the behaviour not to regress
  docs/ARCHITECTURE.md   where this fits in a full training DCS
  docs/APPLICATION_ARCHITECTURE.md   product packages and dependency rules
  docs/DECISIONS.md      why the repo is shaped the way it is
  docs/HMI_INTEGRATION.md   Graphics Designer and operator-station integration
"""


def _areas() -> list[Path]:
    root = SRC / "strategies"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def _projects() -> list[Path]:
    from azeo_control_trainer.azeo_explorer.project_registry import discover_projects

    return discover_projects(ROOT / "projects")


def _cmd_help() -> int:
    print(HELP)
    return 0


def _cmd_list() -> int:
    from azeo_control_trainer.azeo_explorer.project_registry import default_project_path

    areas = _areas()
    projects = _projects()
    default_project = default_project_path(ROOT / "projects")
    if not areas and not projects:
        print("No engineering projects or strategy areas found.")
        return 1
    if projects:
        print("Engineering projects:")
        for project in projects:
            control = project / "control"
            n = len(list(control.glob("*.json"))) if control.is_dir() else 0
            sequence = project / "sequence"
            s = len(list(sequence.glob("*.json"))) if sequence.is_dir() else 0
            marker = "  [default]" if default_project == project.resolve() else ""
            print(
                f"  {project.name:<24} {n} control module(s), "
                f"{s} sequence module(s){marker}"
            )
        print()
    print("Strategy areas:")
    for area in areas:
        control = area / "control"
        n = len(list(control.glob("*.json"))) if control.is_dir() else 0
        displays = area / "displays"
        d = len(list(displays.glob("*.json"))) if displays.is_dir() else 0
        print(f"  {area.name:<24} {n} control module(s), {d} display(s)")
    return 0


def _cmd_plants() -> int:
    """List the simulated processes, and which area each drives."""
    from azeo_control_trainer.plant import registry

    print("Simulated processes (these sit behind SharedDataStore - D2/D7):")
    for cls in registry.all():
        area = f"drives {cls.area}" if cls.area else "no area"
        print(f"  {cls.key:<18} {cls.display_name}  [{area}]")
        print(f"  {'':18} {cls.description}")
    return 0


def _cmd_displays() -> int:
    from azeo_control_trainer.app import _resolve_area
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore

    area = _resolve_area(None)
    root = area / "displays" / "pvm"
    store = DisplayStore(root)
    names = sorted(path.name for path in root.iterdir()
                   if path.is_dir() and not path.name.startswith("_")) \
        if root.is_dir() else []
    if not names:
        print(f"{area.name} ships no operator display.")
        print("Create one in Graphics Designer and publish it to "
              f"{root}.")
        return 1
    print(f"{area.name} operator displays:")
    for name in names:
        draft = store.load_draft(name)
        history = store.history(name)
        latest = history[-1] if history else None
        if draft is not None:
            items = len(draft.pvms) + len(draft.items)
        elif latest is not None:
            document = store.revision_document(name, latest["rev"]) or {}
            items = (len(document.get("pvms", []))
                     + len(document.get("items", [])))
        else:
            items = 0
        release = f"published r{latest['rev']}" if latest else "draft only"
        print(f"  {name:<26} {items:>3} items, {release}")
    return 0


def _cmd_vision() -> int:
    """Colour-vision conformance of the alarm palette, per the style guide."""
    from azeo_control_trainer.core.hmi.theme import hphmi as style
    from azeo_control_trainer.core.hmi.theme import vision

    failed = 0
    for theme in vision.VISION_MODES[:1] and style.THEMES:
        report = vision.report(theme)
        state = "PASS" if report["passes"] else "FAIL"
        print(f"{theme:<17} {state}  "
              f"{report['pairs']} priority pairs, "
              f"{len(report['rescued_by_shape'])} separated by shape alone")
        for finding in report["failed"]:
            print(f"    FAIL {finding.mode}: {finding.first} vs "
                  f"{finding.second}, separation {finding.separation:.0f}")
            failed += 1
        for mode, name, gap, _ok in report["contrast_failures"]:
            print(f"    FAIL {mode}: {name} against the display ground, "
                  f"contrast {gap}")
            failed += 1
    print()
    print("A pair 'separated by shape alone' is the shape coding doing real")
    print("work: colour cannot tell those two apart, and the glyph can.")
    return 1 if failed else 0


def _cmd_upsets() -> int:
    """Process upsets the shipped plant can inject."""
    from azeo_control_trainer.plant import registry

    found = False
    for cls in registry.all():
        if not cls.UPSETS:
            continue
        found = True
        print(f"{cls.display_name} ({cls.key}):")
        for key, description in cls.UPSETS.items():
            print(f"  {key:<20} {description}")
    if not found:
        print("No plant ships an upset scenario.")
    return 0


def _cmd_check() -> int:
    """Verify the plant's tag contract against the area's modules."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.app import _resolve_area
    from azeo_control_trainer.plant import registry
    from azeo_control_trainer.core.strategy.tagdb import TagDatabase

    area = _resolve_area(None)
    db = TagDatabase.from_area(area)
    plant_cls = next((c for c in registry.all() if c.area == area.name), None)
    if plant_cls is None:
        print(f"No plant ships for area '{area.name}' — nothing to check.")
        return 0

    plant = plant_cls()
    problems = plant.verify_against(db)
    print(f"{plant.display_name} vs {area.name} ({len(db)} tag points)")
    print(f"  reads  {len(plant.reads())} controller output(s)")
    print(f"  writes {len(plant.writes())} measurement(s) / feedback(s)")
    print(f"  operator-owned: {len(plant.operator_tags(db))} tag(s)")
    if not problems:
        print("  contract OK - no mismatches")
        return 0
    print(f"  {len(problems)} problem(s):")
    for problem in problems:
        print(f"    {problem}")
    return 1


def _cmd_tagdb(out: str | None) -> int:
    """Dump the derived tag database without opening the UI."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401  (registers blocks)
    from azeo_control_trainer.app import _resolve_area
    from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase

    area = _resolve_area(None)
    db = TagDatabase.from_area(area)
    print(f"{area.name}: {len(db)} points across {len(db.modules())} modules "
          f"({len(db.of_kind(EntryKind.FIELD))} field, "
          f"{len(db.of_kind(EntryKind.TERMINAL))} terminal, "
          f"{len(db.of_kind(EntryKind.PARAMETER))} parameter)")
    if out:
        print("wrote", db.save(out))
    else:
        print("\nField tags the plant must supply:")
        for tag, blocks in sorted(db.field_tags().items()):
            print(f"  {tag:<24} <- {', '.join(blocks)}")
    return 0


def main() -> int:
    from azeo_control_trainer.config.logging_config import (
        application_logger, audit_event, setup_logging,
    )

    setup_logging("launcher")
    command = sys.argv[1] if len(sys.argv) > 1 else "open"
    application_logger("launcher", "run").info(
        "Launcher command: %s", " ".join(sys.argv[1:]) or "<default>")
    audit_event("launcher", "launcher.command", command=command)
    argv = sys.argv[1:]

    if "--help-center" in argv:
        from azeo_control_trainer.core.presentation.product_help import main as help_main
        return help_main()

    if argv and argv[0] in ("--help", "-h", "help", "/?"):
        return _cmd_help()
    if argv and argv[0] == "--list":
        return _cmd_list()
    if argv and argv[0] == "--plants":
        return _cmd_plants()
    if argv and argv[0] == "--displays":
        return _cmd_displays()
    if argv and argv[0] == "--check":
        return _cmd_check()
    if argv and argv[0] == "--vision":
        return _cmd_vision()
    if argv and argv[0] == "--upsets":
        return _cmd_upsets()
    if argv and argv[0] == "--tagdb":
        return _cmd_tagdb(argv[1] if len(argv) > 1 else None)

    from azeo_control_trainer.app import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
