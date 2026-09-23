"""Project discovery and launcher resolution share one configured authority."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.app import _resolve_area  # noqa: E402
from azeo_control_trainer.azeo_explorer.project_registry import (  # noqa: E402
    ProjectRegistryError,
    default_project_path,
    discover_projects,
)


def _project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir()
    (project / "_project.json").write_text("{}", encoding="utf-8")
    return project


def test_registry_selects_default_and_filters_backup_copies(tmp_path) -> None:
    canonical = _project(tmp_path, "Canonical")
    _project(tmp_path, "Canonical.before-change")
    _project(tmp_path, "Canonical.docs-preview")
    (tmp_path / "_registry.json").write_text(json.dumps({
        "default_project": "Canonical",
        "exclude_globs": ["*.before-*", "*.docs-*"],
    }), encoding="utf-8")

    assert discover_projects(tmp_path) == [canonical]
    assert default_project_path(tmp_path) == canonical.resolve()


def test_invalid_default_is_not_silently_replaced(tmp_path) -> None:
    (tmp_path / "_registry.json").write_text(json.dumps({
        "default_project": "Missing",
    }), encoding="utf-8")
    with pytest.raises(ProjectRegistryError, match="default project 'Missing'"):
        default_project_path(tmp_path)


def test_named_repository_project_resolves_to_projects_directory() -> None:
    resolved = _resolve_area("AzeoPlantVirtualController")
    assert resolved == (REPO / "projects" / "AzeoPlantVirtualController").resolve()


def test_unknown_name_does_not_create_an_empty_strategy_area() -> None:
    name = "__definitely_not_an_azeo_project__"
    unwanted = REPO / "src" / "strategies" / name
    assert not unwanted.exists()
    with pytest.raises(FileNotFoundError, match="no engineering project"):
        _resolve_area(name)
    assert not unwanted.exists()
