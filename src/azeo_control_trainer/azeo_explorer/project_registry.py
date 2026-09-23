"""Canonical discovery and default selection for engineering projects.

The repository-level ``projects/_registry.json`` is the single authority for
which project a bare launch opens and which project-like directories are
deliberately ignored.  Keeping this outside the launcher prevents ``run.py``
and the Qt application from disagreeing about what ``[default]`` means.
"""
from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ProjectRegistryError(ValueError):
    """The project registry is malformed or names an unusable default."""


DEFAULT_EXCLUDE_GLOBS = (
    "*.before-*",
    "*.after-*",
    "*.pre-*",
    "*.post-*",
    "*.docs-*",
    "*.backup*",
    "*.bak*",
    "*~",
)


def projects_root() -> Path:
    """Return the source checkout's project directory."""
    from azeo_control_trainer.config.paths import workspace_root
    return workspace_root() / "projects"


def load_project_registry(root: Path | None = None) -> dict[str, Any]:
    """Read and validate the optional repository project registry."""
    root = Path(root) if root is not None else projects_root()
    path = root / "_registry.json"
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectRegistryError(
            f"cannot read project registry {path}: {error}") from error
    if not isinstance(document, Mapping):
        raise ProjectRegistryError("project registry must be a JSON object")
    return dict(document)


def _exclude_globs(registry: Mapping[str, Any]) -> tuple[str, ...]:
    raw = registry.get("exclude_globs", DEFAULT_EXCLUDE_GLOBS)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ProjectRegistryError("project registry exclude_globs must be a list")
    patterns = tuple(str(pattern).strip() for pattern in raw)
    if any(not pattern for pattern in patterns):
        raise ProjectRegistryError(
            "project registry exclude_globs cannot contain an empty pattern")
    return patterns


def _excluded(name: str, patterns: Sequence[str]) -> bool:
    return name.startswith("_") or any(
        fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def discover_projects(root: Path | None = None) -> list[Path]:
    """Return canonical projects, excluding configured backup-like copies."""
    root = Path(root) if root is not None else projects_root()
    if not root.is_dir():
        return []
    registry = load_project_registry(root)
    patterns = _exclude_globs(registry)
    return sorted(
        path for path in root.iterdir()
        if path.is_dir()
        and not _excluded(path.name, patterns)
        and (path / "_project.json").is_file()
    )


def resolve_project(name: str, root: Path | None = None) -> Path | None:
    """Resolve one exact registered project name without accepting a path."""
    candidate_name = str(name or "").strip()
    if not candidate_name or Path(candidate_name).name != candidate_name:
        return None
    for project in discover_projects(root):
        if project.name == candidate_name:
            return project.resolve()
    return None


def default_project_path(root: Path | None = None) -> Path | None:
    """Resolve the registry-selected default; return ``None`` when unconfigured."""
    root = Path(root) if root is not None else projects_root()
    registry = load_project_registry(root)
    raw_name = registry.get("default_project")
    if raw_name in (None, ""):
        return None
    name = str(raw_name).strip()
    project = resolve_project(name, root)
    if project is None:
        raise ProjectRegistryError(
            f"default project {name!r} is missing, excluded, or invalid")
    return project


__all__ = [
    "ProjectRegistryError",
    "default_project_path",
    "discover_projects",
    "load_project_registry",
    "projects_root",
    "resolve_project",
]
