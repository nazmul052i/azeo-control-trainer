"""Process-unit hierarchy contracts for Control Designer's project tree."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.azeo_control_designer.panels import project_tree  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_module(root: Path, name: str, unit: str = "") -> Path:
    path = root / "control" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"name": name, "blocks": [], "wires": []}
    if unit:
        document["metadata"] = {"unit": unit}
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _project(root: Path) -> dict:
    document = {
        "areas": [{
            "name": "PROCESS",
            "area_id": "process",
            "strategies": [
                "control/FIC-1001.json",
                "control/PIC-2001.json",
                "control/ORPHAN.json",
            ],
            "sfc_modules": [],
            "equipment_modules": [],
            "units": [
                {"name": "U100", "description": "Feed", "modules": ["FIC-1001"]},
                {"name": "U200", "description": "Compression", "modules": [
                    "control/PIC-2001.json",
                ]},
            ],
        }],
    }
    (root / "_project.json").write_text(
        json.dumps(document, indent=2), encoding="utf-8")
    return document


def _child(parent, text: str):
    return next(parent.child(i) for i in range(parent.childCount())
                if parent.child(i).text(0) == text)


def _module_names(unit_node) -> list[str]:
    folder = _child(unit_node, "Control Modules")
    return [folder.child(i).text(0) for i in range(folder.childCount())]


def _tree(tmp_path: Path, monkeypatch):
    _app()
    for name in ("FIC-1001", "PIC-2001", "ORPHAN"):
        _write_module(tmp_path, name)
    _project(tmp_path)
    monkeypatch.setattr(project_tree._sio, "STRATEGY_DIR", tmp_path)
    tree = project_tree.ProjectTree(
        plugin=SimpleNamespace(display_name="Training Plant", id="plant"))
    return tree


def test_control_modules_are_grouped_under_process_units(monkeypatch, tmp_path):
    tree = _tree(tmp_path, monkeypatch)
    root = tree._tree.topLevelItem(0)
    area = _child(root, "PROCESS")
    units = _child(area, "Units")

    assert [units.child(i).text(0) for i in range(units.childCount())] == [
        "U100", "U200", "Unassigned",
    ]
    assert _module_names(_child(units, "U100")) == ["FIC-1001"]
    assert _module_names(_child(units, "U200")) == ["PIC-2001"]
    assert _module_names(_child(units, "Unassigned")) == ["ORPHAN"]
    assert area.child(0).text(0) == "Units"
    assert not any(area.child(i).text(0) == "Control Modules"
                   for i in range(area.childCount()))
    tree.deleteLater()


def test_module_can_move_between_unit_and_unassigned(monkeypatch, tmp_path):
    tree = _tree(tmp_path, monkeypatch)
    area = _child(tree._tree.topLevelItem(0), "PROCESS")
    units = _child(area, "Units")
    orphan = _child(_child(_child(units, "Unassigned"), "Control Modules"),
                    "ORPHAN")

    tree._move_strategy_to_unit(orphan, "process", "U200")

    saved = json.loads((tmp_path / "_project.json").read_text(encoding="utf-8"))
    memberships = {unit["name"]: unit["modules"]
                   for unit in saved["areas"][0]["units"]}
    assert memberships["U200"] == ["control/PIC-2001.json", "ORPHAN"]
    units = _child(_child(tree._tree.topLevelItem(0), "PROCESS"), "Units")
    assert "ORPHAN" in _module_names(_child(units, "U200"))
    assert not any(units.child(i).text(0) == "Unassigned"
                   for i in range(units.childCount()))
    tree.deleteLater()


def test_generated_module_adopts_matching_metadata_unit(monkeypatch, tmp_path):
    tree = _tree(tmp_path, monkeypatch)
    generated = _write_module(tmp_path, "TIC-1002", unit="U100")

    tree.register_control_modules([generated], "process")

    saved = json.loads((tmp_path / "_project.json").read_text(encoding="utf-8"))
    area = saved["areas"][0]
    u100 = next(unit for unit in area["units"] if unit["name"] == "U100")
    assert "control/TIC-1002.json" in area["strategies"]
    assert u100["modules"] == ["FIC-1001", "TIC-1002"]
    tree.deleteLater()


def test_new_module_from_unit_folder_is_assigned_there(
    monkeypatch, tmp_path,
):
    tree = _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(
        project_tree.QInputDialog, "getText",
        lambda *_args, **_kwargs: ("LIC-1002", True))

    tree._new_strategy("process", "U100")

    module = json.loads(
        (tmp_path / "LIC-1002.json").read_text(encoding="utf-8"))
    saved = json.loads((tmp_path / "_project.json").read_text(encoding="utf-8"))
    u100 = next(unit for unit in saved["areas"][0]["units"]
                if unit["name"] == "U100")
    assert module["metadata"]["unit"] == "U100"
    assert "LIC-1002" in u100["modules"]
    tree.deleteLater()


def test_module_lifecycle_keeps_unit_membership_current(monkeypatch, tmp_path):
    tree = _tree(tmp_path, monkeypatch)

    def module_item(unit_name: str, module_name: str):
        area = _child(tree._tree.topLevelItem(0), "PROCESS")
        units = _child(area, "Units")
        return _child(_child(_child(units, unit_name), "Control Modules"),
                      module_name)

    monkeypatch.setattr(
        project_tree.QInputDialog, "getText",
        lambda *_args, **_kwargs: ("FIC-1009", True))
    tree._rename_strategy(module_item("U100", "FIC-1001"))
    tree._duplicate_strategy(module_item("U100", "FIC-1009"))

    saved = json.loads((tmp_path / "_project.json").read_text(encoding="utf-8"))
    u100 = next(unit for unit in saved["areas"][0]["units"]
                if unit["name"] == "U100")
    assert "FIC-1001" not in u100["modules"]
    assert "FIC-1009" in u100["modules"]
    assert "FIC-1009_Copy" in u100["modules"]

    monkeypatch.setattr(
        project_tree.QMessageBox, "question",
        lambda *_args, **_kwargs: project_tree.QMessageBox.Yes)
    tree._delete_strategy(module_item("U100", "FIC-1009_Copy"))
    saved = json.loads((tmp_path / "_project.json").read_text(encoding="utf-8"))
    u100 = next(unit for unit in saved["areas"][0]["units"]
                if unit["name"] == "U100")
    assert "FIC-1009_Copy" not in u100["modules"]
    tree.deleteLater()
