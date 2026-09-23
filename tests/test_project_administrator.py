"""Transactional project administration qualification."""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.azeo_explorer.project_admin import (  # noqa: E402
    ProjectAdministrationError, ProjectAdministrator,
)
from azeo_control_trainer.azeo_explorer.project_administrator import (  # noqa: E402
    ProjectAdministratorDialog,
)
from PySide6.QtWidgets import QApplication  # noqa: E402


def _manager(tmp_path: Path) -> ProjectAdministrator:
    (tmp_path / "_registry.json").write_text(json.dumps({
        "schema_version": 1,
        "default_project": "",
    }), encoding="utf-8")
    return ProjectAdministrator(tmp_path)


def test_create_copy_rename_select_and_recoverable_deregister(tmp_path) -> None:
    manager = _manager(tmp_path)
    first = manager.create("FirstProject", description="Qualification")
    assert (first / "control").is_dir()
    assert manager.verify("FirstProject") == []
    manager.set_active("FirstProject")
    assert manager.inventory()[0].active

    copied = manager.copy("FirstProject", "SecondProject")
    first_doc = json.loads((first / "_project.json").read_text())
    copy_doc = json.loads((copied / "_project.json").read_text())
    assert copy_doc["project_id"] != first_doc["project_id"]
    renamed = manager.rename("SecondProject", "RenamedProject")
    assert renamed.name == "RenamedProject"
    destination = manager.deregister("RenamedProject")
    assert destination.is_dir()
    assert not (tmp_path / "RenamedProject").exists()


def test_create_can_preserve_network_without_copying_modules(tmp_path) -> None:
    manager = _manager(tmp_path)
    source = manager.create("NetworkSource")
    document = json.loads((source / "_project.json").read_text())
    document["nodes"] = [{"name": "CTRL-1", "type": "controller"}]
    document["assignments"] = {"control/A.json": "CTRL-1"}
    document["areas"][0]["controller"] = {"name": "CTRL-1"}
    (source / "_project.json").write_text(
        json.dumps(document), encoding="utf-8")
    target = manager.create(
        "NetworkOnly", preserve_network_from="NetworkSource")
    result = json.loads((target / "_project.json").read_text())
    assert result["nodes"] == document["nodes"]
    assert result["areas"][0]["controller"] == {"name": "CTRL-1"}
    assert result["areas"][0]["strategies"] == []
    assert not list((target / "control").glob("*.json"))


def test_backup_restore_checksums_and_blocks_archive_traversal(tmp_path) -> None:
    manager = _manager(tmp_path)
    project = manager.create("BackupSource")
    (project / "control" / "LOOP.json").write_text(
        '{"name":"LOOP"}\n', encoding="utf-8")
    archive = manager.backup("BackupSource")
    assert archive.suffix == ".azeoproject"
    restored = manager.restore(archive, "RestoredProject")
    assert (restored / "control" / "LOOP.json").is_file()
    assert manager.verify("RestoredProject") == []

    unsafe = tmp_path / "unsafe.azeoproject"
    with zipfile.ZipFile(unsafe, "w") as handle:
        handle.writestr("manifest.json", json.dumps({
            "schema_version": 1,
            "type": "azeo.engineering_project_backup",
            "project_name": "Unsafe",
            "files": {},
        }))
        handle.writestr("../escape", "bad")
    with pytest.raises(ProjectAdministrationError, match="unsafe path"):
        manager.restore(unsafe)
    assert not (tmp_path.parent / "escape").exists()


def test_migration_adds_only_missing_project_identity(tmp_path) -> None:
    manager = _manager(tmp_path)
    legacy = tmp_path / "LegacyProject"
    legacy.mkdir()
    (legacy / "_project.json").write_text(
        json.dumps({"areas": [{"name": "LEGACY"}]}), encoding="utf-8")
    preview = manager.migration_preview("LegacyProject")
    assert len(preview) == 3
    assert manager.migrate("LegacyProject") == preview
    document = json.loads((legacy / "_project.json").read_text())
    assert document["schema_version"] == 1
    assert document["name"] == "LegacyProject"
    assert document["project_id"]


def test_project_administrator_dialog_routes_actions_to_service(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path)
    project = manager.create("DialogProject")
    dialog = ProjectAdministratorDialog(manager, project)
    assert dialog.table.rowCount() == 1
    dialog.table.setCurrentCell(0, 0)
    archive = tmp_path / "dialog.azeoproject"
    assert dialog.backup_project(archive) == archive.resolve()
    assert archive.is_file()
    assert dialog.verify_project() == []
    dialog.close()
    app.processEvents()
