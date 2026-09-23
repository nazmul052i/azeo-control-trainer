"""PVM terminology is the only spelling in the tree; saved legacy files still load."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.hmi import compatibility as compat  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.configurator.model import (  # noqa: E402
    PvmConfiguration, PvmProperty, PropertyGroup,
)
from azeo_control_trainer.core.hmi.pvms.properties import PropertyResolver  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
#: The retired spelling, taken from the one module allowed to assemble it.
LEGACY = compat.LEGACY_PVM_TERM
LEGACY_PREFIX = compat.LEGACY_SCOPE_PREFIX
LEGACY_WORD = re.compile(r"\b" + compat.LEGACY_PVM_UPPER_TERM + r"s?\b")


@pytest.mark.parametrize("prefix", [compat.PVM_SCOPE_PREFIX, compat.LEGACY_SCOPE_PREFIX])
def test_current_and_saved_property_names_resolve_without_rewriting(prefix):
    config = PvmConfiguration("Vessel", [PropertyGroup("General", properties=[
        PvmProperty("Title", default="Feed vessel"),
        PvmProperty("Caption", default=f"{prefix}Title"),
    ])])
    saved = config.to_dict()
    assert config.value_of("Caption", {}) == "Feed vessel"
    assert UserPvmLibrary._resolve(f"Unit: {prefix}Title", config, {}, None) == "Unit: Feed vessel"
    resolver = PropertyResolver(pvm_properties={"Title": "Feed vessel"})
    assert resolver.resolve_property({"text": f"{prefix}Title"}, "text").value == "Feed vessel"
    assert config.to_dict() == saved
    assert config.rename_property("Title", "UnitTitle")
    assert config.property("Caption").default == f"{prefix}UnitTitle"
    assert config.value_of("Caption", {}) == "Feed vessel"


def test_public_help_and_library_use_pvm():
    from azeo_control_trainer.core.hmi.pvms.library_catalog import ArtifactRecord
    record = ArtifactRecord("Vessel", "pvm", "", "Ready", (), (), (), 0, 100, 100, True)
    assert record.kind_title == "PVM Class"
    guide = (ROOT / "docs/PVM_FACEPLATE_TUTORIAL.md").read_text(encoding="utf-8")
    assert "Process Visualization Module" in guide
    assert "Pvm.PVPath" in guide
    assert not LEGACY_WORD.search(guide)
    training_source = (ROOT / "tools/build_training_slides.mjs").read_text(
        encoding="utf-8")
    assert not LEGACY_WORD.search(training_source)


def test_saved_and_audit_text_uses_pvm_while_legacy_format_keys_remain_readable():
    findings = []
    for directory in (ROOT / "projects", ROOT / "src/strategies",
                      ROOT / "archive", ROOT / "docs/uiux"):
        for path in directory.rglob("*.json"):
            if LEGACY_WORD.search(path.read_text(encoding="utf-8-sig")):
                findings.append(str(path.relative_to(ROOT)))
    assert not findings


def test_legacy_library_file_loads_under_current_names(tmp_path):
    legacy_entry = {
        "definition_kind": LEGACY, "folder": compat.LEGACY_DEFAULT_FOLDER,
        "items": [{"kind": "rect", "x": 0, "y": 0, "w": 20, "h": 20,
                   "user_" + LEGACY: "SavedVessel", LEGACY + "_index": 0,
                   LEGACY + "_choices": {"Size": "Large"}}],
    }
    library_dir = tmp_path / "_library"
    library_dir.mkdir()
    (library_dir / compat.LEGACY_PVM_LIBRARY_FILENAME).write_text(
        json.dumps({"SavedVessel": legacy_entry}), encoding="utf-8")
    library = UserPvmLibrary(tmp_path)
    entry = library.entries["SavedVessel"]
    assert entry["definition_kind"] == "pvm"
    assert entry["folder"] == compat.PVM_DEFAULT_FOLDER
    assert entry["items"][0]["user_pvm"] == "SavedVessel"
    assert entry["items"][0]["pvm_index"] == 0
    assert entry["items"][0]["pvm_choices"] == {"Size": "Large"}
    assert "user_" + LEGACY not in entry["items"][0]
    assert library.folders() == (compat.PVM_DEFAULT_FOLDER,)
    assert library.folder_title(compat.LEGACY_DEFAULT_FOLDER) == compat.PVM_DEFAULT_FOLDER
    assert library.folder_title("Site vessels") == "Site vessels"
    from azeo_control_trainer.core.hmi.pvms.library_catalog import LibraryCatalog
    record = LibraryCatalog(tmp_path).records()[0]
    assert (record.kind, record.folder) == ("pvm", compat.PVM_DEFAULT_FOLDER)
    library.save()
    assert library.path.name == compat.PVM_LIBRARY_FILENAME
    saved = library.path.read_text(encoding="utf-8")
    assert LEGACY not in saved


def test_legacy_display_document_normalizes_once_and_saves_current_keys():
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    legacy = {
        "display": "Unit",
        LEGACY + "s": [{"id": "p1", "class": "AI/dynamo_inline", "params": {"path": "M/B"},
                        "x": 0, "y": 0, "w": 100, "h": 40, "layer": LEGACY + "s"}],
        "items": [{"kind": "nested_" + LEGACY, "class": "Child", "user_" + LEGACY: "Parent",
                   LEGACY + "_link": "linked", LEGACY + "_overrides": {"fill": "#fff"},
                   "props": {"text": {"kind": LEGACY, "ref": LEGACY_PREFIX + "Title"}}}],
    }
    display = PvmDisplay.from_dict(legacy)
    assert display.pvms[0]["id"] == "p1"
    assert display.pvms[0]["layer"] == "pvms"
    item = display.items[0]
    assert item["kind"] == "nested_pvm"
    assert item["user_pvm"] == "Parent"
    assert item["pvm_link"] == "linked"
    assert item["pvm_overrides"] == {"fill": "#fff"}
    assert item["props"]["text"]["kind"] == "pvm"
    assert item["props"]["text"]["ref"] == LEGACY_PREFIX + "Title"
    document = display.to_dict()
    assert "pvms" in document and LEGACY + "s" not in document
    assert LEGACY + "_" not in json.dumps(document)


def test_legacy_folders_and_configuration_files_are_found(tmp_path):
    area = tmp_path / "area"
    legacy_root = area / "displays" / compat.LEGACY_DISPLAY_FOLDER
    legacy_root.mkdir(parents=True)
    assert compat.display_root(area) == legacy_root
    assert compat.is_display_root(legacy_root)
    current_root = area / "displays" / compat.PVM_DISPLAY_FOLDER
    current_root.mkdir()
    assert compat.display_root(area) == current_root
    assert compat.is_display_document_path(
        "displays/" + compat.LEGACY_DISPLAY_FOLDER + "/Unit/draft.json")
    assert compat.is_display_document_path("displays/pvm/Unit/draft.json")

    legacy_cfg = current_root / compat.LEGACY_CONFIG_FOLDER
    legacy_cfg.mkdir()
    legacy_file = legacy_cfg / ("Vessel" + compat.LEGACY_CONFIG_SUFFIX)
    legacy_file.write_text(json.dumps({LEGACY + "_class": "Vessel", "groups": []}), encoding="utf-8")
    assert compat.configuration_document_path(current_root, "Vessel") == legacy_file
    assert compat.configuration_document_paths(current_root / compat.PVM_CONFIG_FOLDER) == [legacy_file]
    loaded = PvmConfiguration.load(legacy_file)
    assert loaded.pvm_class == "Vessel"
    assert loaded.to_dict()["pvm_class"] == "Vessel"
    saved = loaded.save(current_root / compat.PVM_CONFIG_FOLDER)
    assert saved.name == "Vessel" + compat.PVM_CONFIG_SUFFIX
    assert compat.configuration_document_path(current_root, "Vessel") == saved
    captured_key = "displays/pvm/" + compat.LEGACY_CONFIG_FOLDER + "/Vessel" + compat.LEGACY_CONFIG_SUFFIX
    assert compat.split_configuration_document_path(captured_key) == ("displays/pvm", "Vessel")
    assert compat.find_configuration_document({captured_key: {"x": 1}}, "displays/pvm", "Vessel") == {"x": 1}
    assert compat.configuration_documents({captured_key: {"x": 1}}, "displays/pvm") == {"Vessel": {"x": 1}}


def test_identity_seeds_are_stable_across_the_rename():
    # A renamed seed would hand every unpinned class definition a new id.
    assert compat.USER_DEFINITION_ID_SEED == "azeo:user-" + LEGACY + "-definition:"
    assert compat.USER_INSTANCE_ID_SEED == "azeo:user-" + LEGACY + "-instance:"


def test_authored_simulator_text_has_no_vendor_product_references():
    forbidden = re.compile(r"delta[\s_-]*v", re.I)
    findings = []
    for directory in (ROOT / "AzeoPlantSimulator/azeoplant", ROOT / "AzeoPlantSimulator/cpp"):
        for path in directory.rglob("*"):
            if path.suffix in {".py", ".md", ".hpp", ".cpp"}:
                if forbidden.search(path.read_text(encoding="utf-8-sig")):
                    findings.append(str(path.relative_to(ROOT)))
    assert not findings


_TERM_EXCLUDED = ("dist/",)
_TERM_BINARY = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pyd", ".dll", ".exe", ".zip",
                ".sha256", ".woff", ".woff2", ".ttf", ".otf")
_BLOB = re.compile(r"[A-Za-z0-9+/=]{40,}")


def _retired_term_matches(text: str):
    """Occurrences of the retired term outside ordinary words and encoded data."""
    pattern = re.compile(r"(?<![A-Za-z])" + LEGACY + "|" + compat.LEGACY_SCOPE_NAME
                         + r"|(?<![A-Z])" + compat.LEGACY_PVM_UPPER_TERM)
    blobs = [m.span() for m in _BLOB.finditer(text)
             if sum(ch.isdigit() for ch in m.group(0)) >= 5]
    for match in pattern.finditer(text):
        if any(start <= match.start() < end for start, end in blobs):
            continue
        yield match


def test_active_tree_carries_no_retired_term():
    """The rename is durable: source, tests, tools, docs and project data all use PVM."""
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True).stdout.decode("utf-8")
    findings = []
    me = Path(__file__).resolve()
    for relative in filter(None, listing.split("\0")):
        path = ROOT / relative
        if (relative.startswith(_TERM_EXCLUDED) or relative.lower().endswith(_TERM_BINARY)
                or "LICENSE" in path.name or path.resolve() == me
                or relative.endswith("core/hmi/compatibility.py") or not path.is_file()):
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        text = raw.decode("utf-8", "replace")
        for match in _retired_term_matches(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{relative}:{line}: {text[max(0, match.start() - 20):match.end() + 20]!r}")
            break
    assert not findings, "\n".join(findings)


def test_old_and_new_optional_catalog_metadata_keep_the_same_io_map(tmp_path):
    from azeo_control_trainer.connectivity.fieldio.opcua_catalog import load_signal_catalog
    row = {"name": "LT-1001", "node_id": "ns=2;s=LT-1001", "kind": "AI",
           "eu": "%", "lo": 0, "hi": 100}
    path = tmp_path / "catalog.json"
    legacy_metadata = "delta" + "v"
    path.write_text(json.dumps([dict(row, **{legacy_metadata: {"dst": "LT-1001"}})]))
    before = load_signal_catalog(path)
    path.write_text(json.dumps([dict(row, controller_mapping={"dst": "LT-1001"})]))
    assert load_signal_catalog(path) == before


def test_binding_picker_finds_saved_references_but_offers_pvm_names():
    from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import BindingCatalog, BindingKind
    catalog = BindingCatalog()
    catalog.add_configuration(PvmConfiguration("Vessel", [PropertyGroup("General", properties=[
        PvmProperty("Title", default="Feed vessel"),
    ])]))
    offered = catalog.by_kind(BindingKind.CLASS_PROPERTY)
    assert offered and all(row.reference.startswith("Pvm.") for row in offered)
    assert catalog.find(BindingKind.CLASS_PROPERTY, LEGACY_PREFIX + "Title") is offered[0]
    assert catalog.find(BindingKind.CLASS_PROPERTY, "Pvm.Title") is offered[0]


def test_pvm_script_scope_uses_the_same_restricted_graphic_access():
    from PySide6.QtWidgets import QApplication, QGraphicsRectItem
    from azeo_control_trainer.core.hmi.pvms.scripting import GraphicsScriptRuntime, ScriptContext
    app = QApplication.instance() or QApplication([])
    item = QGraphicsRectItem(0, 0, 10, 10)
    item.setX(12)
    context = ScriptContext(item=item, scope_values={LEGACY_PREFIX + "Custom": 3})
    runtime = GraphicsScriptRuntime()
    modern = runtime.run("Pvm.HorizontalPosition = 25; return Pvm.HorizontalPosition;", context)
    legacy = runtime.run("return " + LEGACY_PREFIX + "HorizontalPosition;", context)
    assert modern.ok and modern.value == 25, modern
    assert legacy.ok and legacy.value == 25, legacy
    assert item.x() == 25
    assert runtime.run("return Pvm.Custom;", context).value == 3
    assert runtime.run("Pvm.Custom = 7; return " + LEGACY_PREFIX + "Custom;", context).value == 7
    assert context.scope_values[LEGACY_PREFIX + "Custom"] == 7
    assert not runtime.run("return require('fs');", context).ok
    app.processEvents()
