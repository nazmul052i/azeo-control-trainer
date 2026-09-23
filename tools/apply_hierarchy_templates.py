"""Configure a project's operator displays from the ISA-101 L1-L4 templates.

    python tools/apply_hierarchy_templates.py SPEC [--check] [--publish] [--render DIR]

``SPEC`` is a JSON file inside a project's ``engineering/`` folder (see
``projects/AzeoPlantVirtualController/engineering/display_hierarchy.json``).
It names the display root, the release environment and workstations, and for
every display its level, parent, child and the content that fills the slots of
that level's template: text items, PVM bindings, trend pens, alarm scope and
table rows, all keyed by the template's item ids.

Every generated document is the built-in template document of its level with
only slot content replaced.  Geometry, item ids, kinds, roles, sizes and PVM
classes are compared with a pristine template instance and any other difference
refuses the display, so a spec cannot drift a display away from the template.
Every binding is resolved against the project's tag database (module, block,
block type and terminal) before anything is written.

Without options the drafts are written.  ``--check`` only reports; ``--publish``
runs Graphics Designer's release verifier over the saved drafts and publishes each
one through the ordinary gate; ``--render DIR`` renders the documents through
the operator viewer for inspection.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import (  # noqa: E402
    BUILTIN_TEMPLATE_NAMES, hierarchy_document,
)
from azeo_control_trainer.core.hmi.pvms.publishing import (  # noqa: E402
    CURRENT_DISPLAY_SCHEMA_VERSION, DisplayStore, Finding, PvmDisplay,
)

log = logging.getLogger("apply_hierarchy_templates")

#: Per item kind, the fields a spec may change; everything else is template.
CONTENT_FIELDS = {
    "text": {"text"},
    "chart": {"pens"},
    "alarm_list": {"path_prefix", "priority_min"},
    "table": {"rows"},
    "display_link": {"target"},
}
PVM_CONTENT_FIELDS = {"params", "label"}
DOCUMENT_CONTENT_FIELDS = {"description"}
SPEC_SECTIONS = {"texts": "text", "pens": "chart", "alarms": "alarm_list", "tables": "table"}


class SpecError(ValueError):
    """The spec asks for something the template cannot carry."""


# ------------------------------------------------------------------ build
def load_spec(path: Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(spec.get("displays"), dict) or not spec["displays"]:
        raise SpecError(f"{path} names no displays")
    return spec


def _items_by_id(document: PvmDisplay) -> dict[str, dict]:
    index = {}
    for item in document.items:
        ident = str(item.get("id", ""))
        if ident in index:
            raise SpecError(f"template item id {ident!r} is not unique")
        index[ident] = item
    return index


def build(name: str, entry: dict) -> PvmDisplay:
    """The template document of ``entry['level']`` with the spec's content."""
    level = int(entry.get("level", 0))
    parent = str(entry.get("parent", "") or "")
    child = str(entry.get("child", "") or "")
    document = hierarchy_document(level, name, parent=parent, child=child)
    # A configured hierarchy is a newly authored document, not a legacy file
    # whose absent schema must be interpreted by the compatibility reader.
    document.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
    document.description = str(entry.get("description", "") or document.description)
    items = _items_by_id(document)
    for section, kind in SPEC_SECTIONS.items():
        for ident, value in (entry.get(section) or {}).items():
            item = items.get(ident)
            if item is None:
                raise SpecError(f"{name}: L{level} template has no item {ident!r} for {section}")
            if item.get("kind") != kind:
                raise SpecError(f"{name}: item {ident!r} is a {item.get('kind')}, not a {kind}")
            if section == "texts":
                item["text"] = str(value)
            elif section == "pens":
                item["pens"] = [{"path": str(path), "label": str(label)} for label, path in value]
            elif section == "alarms":
                for key in value:
                    if key not in CONTENT_FIELDS["alarm_list"]:
                        raise SpecError(f"{name}: alarm list {ident!r} cannot set {key!r}")
                item["path_prefix"] = str(value.get("path_prefix", item.get("path_prefix", "")))
                item["priority_min"] = int(value.get("priority_min", item.get("priority_min", 0)))
            else:
                keys = {key for row in item.get("rows", ()) for key in row}
                rows = []
                for row in value:
                    if set(row) != keys:
                        raise SpecError(f"{name}: table {ident!r} rows carry {sorted(keys)}, not {sorted(row)}")
                    rows.append(dict(row))
                item["rows"] = rows
    pvms = {pvm.id: pvm for pvm in document.pvms}
    for ident, value in (entry.get("pvms") or {}).items():
        pvm = pvms.get(ident)
        if pvm is None:
            raise SpecError(f"{name}: L{level} template has no PVM slot {ident!r}")
        path = str(value.get("path", "")).strip()
        if path.count("/") != 1:
            raise SpecError(f"{name}: PVM {ident!r} needs a MODULE/BLOCK path, got {path!r}")
        pvms[ident] = replace(pvm, params={**pvm.params, "path": path},
                              label=str(value.get("label", pvm.label)))
    document.pvms = [pvms[pvm.id] for pvm in document.pvms]
    return document


# ---------------------------------------------------------------- parity
def template_differences(document: PvmDisplay, name: str, entry: dict) -> list[str]:
    """Every way ``document`` departs from its pristine template beyond content."""
    pristine_document = hierarchy_document(
        int(entry.get("level", 0)), name,
        parent=str(entry.get("parent", "") or ""),
        child=str(entry.get("child", "") or ""))
    pristine_document.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
    pristine = pristine_document.to_dict()
    actual = document.to_dict()
    problems = []
    for key in sorted(set(pristine) | set(actual)):
        if key in ("items", "pvms") or key in DOCUMENT_CONTENT_FIELDS:
            continue
        if pristine.get(key) != actual.get(key):
            problems.append(f"document field {key!r} changed")
    for family, allowed in (("items", None), ("pvms", PVM_CONTENT_FIELDS)):
        before, after = pristine.get(family, []), actual.get(family, [])
        if len(before) != len(after):
            problems.append(f"{family}: template has {len(before)}, document has {len(after)}")
            continue
        for index, (old, new) in enumerate(zip(before, after)):
            if set(old) != set(new):
                problems.append(f"{family}[{index}] ({old.get('id')}): fields {sorted(set(old) ^ set(new))} added or removed")
                continue
            fields = allowed if allowed is not None else CONTENT_FIELDS.get(str(old.get("kind")), set())
            for key in old:
                if old[key] != new[key] and key not in fields:
                    problems.append(f"{family}[{index}] ({old.get('id')}): {key!r} is template geometry, not content")
    return problems


# -------------------------------------------------------------- bindings
def load_graphs(project: Path) -> dict:
    """Every control, sequence and equipment module of ``project`` by name."""
    from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401 - block registrations

    paths = list(project.glob("*.json"))
    for namespace in ("control", "sequence", "equipment"):
        folder = project / namespace
        if folder.is_dir():
            paths.extend(folder.rglob("*.json"))
    graphs = {}
    for path in sorted(set(paths)):
        if path.name == "_project.json" or "versions" in path.parts:
            continue
        try:
            graph, _comments = load_strategy(str(path), remember=False)
        except Exception as error:                        # noqa: BLE001 - reported below
            log.warning("skipping %s: %s", path.name, error)
            continue
        if graph.blocks:
            graphs[graph.name] = graph
    return graphs


def binding_index(graphs: dict) -> tuple[dict, set]:
    from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase

    database = TagDatabase.from_graphs(graphs.values())
    blocks, terminals = {}, set()
    for entry in database.of_kind(EntryKind.TERMINAL):
        blocks[(entry.module, entry.block)] = entry.block_type
        terminals.add(entry.path)
    return blocks, terminals


def _paths(document: PvmDisplay) -> list[tuple[str, str]]:
    from azeo_control_trainer.core.hmi.pvms.elements import element_paths

    found = []
    for item in document.items:
        for path in element_paths(item):
            found.append((str(item.get("id")), path))
    return found


def binding_problems(document: PvmDisplay, blocks: dict, terminals: set,
                     displays: set) -> list[str]:
    problems = []
    for pvm in document.pvms:
        path = str(pvm.params.get("path", ""))
        parts = path.split("/")
        if len(parts) != 2 or tuple(parts) not in blocks:
            problems.append(f"PVM {pvm.id}: no block at {path!r}")
        elif blocks[tuple(parts)] != pvm.block_type:
            problems.append(f"PVM {pvm.id}: {path} is a {blocks[tuple(parts)]} block, slot needs {pvm.block_type}")
    for ident, path in _paths(document):
        if path not in terminals:
            problems.append(f"{ident}: no terminal at {path!r}")
    for item in document.items:
        if item.get("kind") == "display_link" and str(item.get("target", "")) not in displays:
            problems.append(f"{item.get('id')}: links to unknown display {item.get('target')!r}")
        if item.get("kind") == "alarm_list":
            prefix = str(item.get("path_prefix", ""))
            if prefix and not any(module.startswith(prefix) for module, _block in blocks):
                problems.append(f"{item.get('id')}: alarm scope {prefix!r} matches no module")
    return problems


# ---------------------------------------------------------------- publish
def studio_findings(project: Path, display_root: str, names, graphs: dict) -> dict[str, list[Finding]]:
    from azeo_control_trainer.azeo_graphics_designer.release_check import verify_displays

    relative = [f"{display_root}/{name}/draft.json" for name in names]
    findings: dict[str, list[Finding]] = {name: [] for name in names}
    for row in verify_displays(project, relative, graphs):
        name = Path(row["path"]).parent.name
        findings.setdefault(name, []).append(
            Finding(str(row.get("severity", "")), str(row.get("message", "")), str(row.get("item", "") or "")))
    return findings


def render(document: PvmDisplay, graphs: dict, display_root: Path, out_dir: Path, themes) -> list[Path]:
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

    app = QApplication.instance() or QApplication([])
    written = []
    for theme in themes:
        view = PvmDisplayView(document.to_dict(), lambda: graphs, theme=theme,
                              config_root=display_root, live=False)
        view.resize(document.width + 10, document.height + 40)
        view.show()
        app.processEvents()
        try:
            for _ in range(3):
                view.refresh()
            target = out_dir / f"{document.name} [{theme}].png"
            if not view.grab().save(str(target)):
                raise RuntimeError(f"could not write {target}")
            written.append(target)
        finally:
            view.close()
            view.deleteLater()
            app.processEvents()
    return written


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--project", type=Path, help="project folder (default: the spec's grandparent)")
    parser.add_argument("--check", action="store_true", help="build and verify without writing")
    parser.add_argument("--publish", action="store_true", help="verify the saved drafts and publish them")
    parser.add_argument("--render", type=Path, help="write viewer renders of every document here")
    parser.add_argument("--themes", default="silver,hpgray")
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.disable(logging.WARNING)                      # 150 compiles emit expected BKCAL notes

    spec = load_spec(options.spec)
    project = (options.project or options.spec.resolve().parents[1]).resolve()
    display_root = project / str(spec.get("display_root", "displays/pvm"))
    store = DisplayStore(display_root)
    names = list(spec["displays"])
    known = set(names) | {path.name for path in display_root.iterdir()
                          if (path / "draft.json").is_file()}

    print(f"project {project.name}: {len(names)} display(s) from {', '.join(BUILTIN_TEMPLATE_NAMES)}")
    print("loading modules...", flush=True)
    graphs = load_graphs(project)
    blocks, terminals = binding_index(graphs)
    print(f"  {len(graphs)} modules, {len(blocks)} blocks, {len(terminals)} terminals", flush=True)

    documents, failures = {}, 0
    for name in names:
        entry = spec["displays"][name]
        try:
            document = build(name, entry)
        except SpecError as error:
            print(f"[FAIL] {name}: {error}")
            failures += 1
            continue
        problems = template_differences(document, name, entry) + binding_problems(document, blocks, terminals, known)
        if problems:
            failures += 1
            print(f"[FAIL] {name}:")
            for problem in problems:
                print(f"       {problem}")
            continue
        documents[name] = document
        bound = len(document.pvms) + len(_paths(document))
        print(f"[ok]   {name}: L{document.level} template, {len(document.items)} items, "
              f"{len(document.pvms)} PVMs, {bound} bindings resolved")
    if failures:
        print(f"{failures} display(s) refused; nothing written")
        return 1
    if options.check:
        return 0

    for name, document in documents.items():
        store.save_draft(document)
    print(f"saved {len(documents)} draft(s) under {display_root}")

    if options.render:
        options.render.mkdir(parents=True, exist_ok=True)
        themes = [theme.strip() for theme in options.themes.split(",") if theme.strip()]
        for document in documents.values():
            for path in render(document, graphs, display_root, options.render, themes):
                print(f"rendered {path}")

    if options.publish:
        print("verifying drafts with the Graphics Designer release checker...", flush=True)
        findings = studio_findings(project, str(spec.get("display_root", "displays/pvm")), list(documents), graphs)
        refused = 0
        for name, document in documents.items():
            rows = findings.get(name, [])
            blocking = [row for row in rows if row.blocks_publish]
            if blocking:
                refused += 1
                print(f"[FAIL] {name}: {len(blocking)} blocking finding(s)")
                for row in blocking:
                    print(f"       {row.severity}: {row.message} {row.item}")
                continue
            entry = store.publish(
                document, env=str(spec.get("environment", "TEST")),
                workstations=list(spec.get("workstations", ())), by="apply_hierarchy_templates",
                resolve=lambda path: tuple(str(path).split("/")) in blocks, findings=rows)
            print(f"[ok]   {name}: published rev {entry['rev']} to {entry['workstations'] or 'all stations'}"
                  f"{f', {len(rows)} advisory finding(s)' if rows else ''}")
            for row in rows:
                print(f"       {row.severity}: {row.message} [{row.item}]")
        if refused:
            print(f"{refused} display(s) refused by the publish gate")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
