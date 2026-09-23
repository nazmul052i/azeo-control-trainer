"""Transactional engineering-project lifecycle services.

This is the repository equivalent of a DCS database administrator: projects
can be created, copied, selected, backed up, restored, registered, and moved
to a recoverable deregistered area.  No Qt dependency lives here, so every
operation is qualification-testable.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import display_root as resolve_display_root

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .project_registry import (
    DEFAULT_EXCLUDE_GLOBS, discover_projects, load_project_registry,
    projects_root,
)


_PROJECT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")


class ProjectAdministrationError(RuntimeError):
    """A lifecycle request was unsafe, incomplete, or inconsistent."""


@dataclass(frozen=True)
class ProjectRecord:
    name: str
    path: Path
    active: bool
    schema_version: int
    modules: int
    displays: int
    modified: float
    status: str = "Ready"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _validate_name(name: str) -> str:
    candidate = str(name or "").strip()
    if not _PROJECT_NAME.fullmatch(candidate):
        raise ProjectAdministrationError(
            "project names must start with a letter, contain 2–64 letters, "
            "digits, dots, hyphens, or underscores, and contain no path")
    return candidate


def _read_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectAdministrationError(
            f"cannot read project document {path}: {error}") from error
    if not isinstance(document, Mapping):
        raise ProjectAdministrationError(
            f"project document {path} must contain a JSON object")
    return dict(document)


class ProjectAdministrator:
    """Manage the projects below one canonical projects directory."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or projects_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / "_registry.json"
        self.backup_root = self.root / "_backups"
        self.deregistered_root = self.root / "_deregistered"

    def _registry(self) -> dict[str, Any]:
        document = load_project_registry(self.root)
        document.setdefault("schema_version", 1)
        document.setdefault("exclude_globs", list(DEFAULT_EXCLUDE_GLOBS))
        return document

    def inventory(self) -> list[ProjectRecord]:
        registry = self._registry()
        active = str(registry.get("default_project") or "")
        records = []
        for path in discover_projects(self.root):
            try:
                document = _read_document(path / "_project.json")
                status = "Ready"
            except ProjectAdministrationError as error:
                document = {}
                status = str(error)
            modules = sum(1 for folder in ("control", "sequence")
                          for _ in (path / folder).glob("*.json"))
            display_root = resolve_display_root(path)
            displays = sum(1 for item in display_root.iterdir()
                           if item.is_dir() and not item.name.startswith("_")) \
                if display_root.is_dir() else 0
            records.append(ProjectRecord(
                name=path.name,
                path=path,
                active=path.name == active,
                schema_version=int(document.get("schema_version", 0) or 0),
                modules=modules,
                displays=displays,
                modified=(path / "_project.json").stat().st_mtime,
                status=status,
            ))
        return records

    def verify(self, name: str) -> list[str]:
        path = self._project(name)
        document = _read_document(path / "_project.json")
        issues: list[str] = []
        areas = document.get("areas")
        if not isinstance(areas, list) or not areas:
            issues.append("project must define at least one area")
        nodes = document.get("nodes", [])
        if not isinstance(nodes, list):
            issues.append("nodes must be a list")
        assignments = document.get("assignments", {})
        if not isinstance(assignments, Mapping):
            issues.append("assignments must be an object")
        if isinstance(areas, list):
            for area in areas:
                if not isinstance(area, Mapping):
                    issues.append("each area must be an object")
                    continue
                for key in ("strategies", "sfc_modules", "equipment_modules"):
                    for relative in area.get(key, []) or []:
                        if not (path / str(relative)).is_file():
                            issues.append(f"missing configured module {relative}")
        return issues

    def _project(self, name: str) -> Path:
        candidate = self.root / _validate_name(name)
        if not candidate.is_dir() or not (candidate / "_project.json").is_file():
            raise ProjectAdministrationError(f"project {name!r} is not registered")
        return candidate.resolve()

    def _unused_target(self, name: str) -> Path:
        target = self.root / _validate_name(name)
        if target.exists():
            raise ProjectAdministrationError(f"project {name!r} already exists")
        return target

    def _staging(self, name: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=f"._{name}-", dir=self.root))

    def create(self, name: str, *, description: str = "",
               preserve_network_from: str | None = None) -> Path:
        name = _validate_name(name)
        target = self._unused_target(name)
        staging = self._staging(name)
        try:
            area: dict[str, Any] = {
                "name": name.upper(),
                "area_id": name.lower().replace("-", "_"),
                "description": str(description),
                "strategies": [],
                "sfc_modules": [],
                "equipment_modules": [],
            }
            document: dict[str, Any] = {
                "schema_version": 1,
                "project_id": str(uuid.uuid4()),
                "name": name,
                "created_at": _utc_now(),
                "areas": [area],
                "nodes": [],
                "assignments": {},
            }
            if preserve_network_from:
                source = _read_document(
                    self._project(preserve_network_from) / "_project.json")
                document["nodes"] = source.get("nodes", [])
                document["assignments"] = source.get("assignments", {})
                source_areas = source.get("areas", [])
                if source_areas:
                    inherited = source_areas[0]
                    for key in ("controller", "virtual_io", "eioc", "console"):
                        if key in inherited:
                            area[key] = inherited[key]
            for relative in (
                    "control", "sequence", "displays/pvm", "virtual_io/scenarios",
                    "docs"):
                (staging / relative).mkdir(parents=True, exist_ok=True)
            _write_json_atomic(staging / "_project.json", document)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target.resolve()

    def copy(self, source_name: str, target_name: str) -> Path:
        source = self._project(source_name)
        target = self._unused_target(target_name)
        staging = self._staging(target.name)
        shutil.rmtree(staging)
        try:
            shutil.copytree(source, staging)
            document = _read_document(staging / "_project.json")
            document.update({
                "project_id": str(uuid.uuid4()),
                "name": target.name,
                "copied_from": source.name,
                "created_at": _utc_now(),
            })
            _write_json_atomic(staging / "_project.json", document)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target.resolve()

    def rename(self, old_name: str, new_name: str) -> Path:
        source = self._project(old_name)
        target = self._unused_target(new_name)
        registry = self._registry()
        original_document = _read_document(source / "_project.json")
        os.replace(source, target)
        try:
            document = _read_document(target / "_project.json")
            document["name"] = target.name
            document["renamed_at"] = _utc_now()
            _write_json_atomic(target / "_project.json", document)
            if registry.get("default_project") == source.name:
                registry["default_project"] = target.name
                _write_json_atomic(self.registry_path, registry)
        except Exception:
            _write_json_atomic(target / "_project.json", original_document)
            os.replace(target, source)
            raise
        return target.resolve()

    def set_active(self, name: str) -> Path:
        project = self._project(name)
        registry = self._registry()
        registry["default_project"] = project.name
        registry["selected_at"] = _utc_now()
        _write_json_atomic(self.registry_path, registry)
        return project

    def deregister(self, name: str) -> Path:
        project = self._project(name)
        if self._registry().get("default_project") == project.name:
            raise ProjectAdministrationError(
                "the active project cannot be deregistered; select another first")
        self.deregistered_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.deregistered_root / f"{project.name}-{stamp}"
        os.replace(project, destination)
        return destination.resolve()

    def register_directory(self, source: Path | str,
                           name: str | None = None) -> Path:
        source = Path(source).expanduser().resolve()
        if not (source / "_project.json").is_file():
            raise ProjectAdministrationError(
                "a registered project directory needs _project.json")
        target = self._unused_target(name or source.name)
        staging = self._staging(target.name)
        shutil.rmtree(staging)
        try:
            shutil.copytree(source, staging)
            _read_document(staging / "_project.json")
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target.resolve()

    @staticmethod
    def _archive_members(project: Path):
        for path in sorted(project.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project)
            if any(part.startswith(".") and part.endswith(".tmp")
                   for part in relative.parts):
                continue
            yield path, relative

    def backup(self, name: str, destination: Path | str | None = None) -> Path:
        project = self._project(name)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        if destination is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            destination = self.backup_root / project.name / (
                f"{project.name}-{stamp}.azeoproject")
        destination = Path(destination).expanduser().resolve()
        if destination == project or project in destination.parents:
            raise ProjectAdministrationError(
                "a project backup must be stored outside the project itself")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent)
        os.close(fd)
        hashes: dict[str, str] = {}
        try:
            with zipfile.ZipFile(
                    temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path, relative in self._archive_members(project):
                    data = path.read_bytes()
                    member = PurePosixPath("project", *relative.parts).as_posix()
                    archive.writestr(member, data)
                    hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
                archive.writestr("manifest.json", json.dumps({
                    "schema_version": 1,
                    "type": "azeo.engineering_project_backup",
                    "project_name": project.name,
                    "created_at": _utc_now(),
                    "files": hashes,
                }, indent=2) + "\n")
            os.replace(temporary, destination)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return destination

    @staticmethod
    def _safe_member(name: str) -> bool:
        path = PurePosixPath(name)
        return (not path.is_absolute() and ".." not in path.parts
                and path.parts and path.parts[0] in {"project", "manifest.json"})

    def restore(self, archive_path: Path | str,
                project_name: str | None = None) -> Path:
        source = Path(archive_path).expanduser().resolve()
        try:
            archive = zipfile.ZipFile(source, "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise ProjectAdministrationError(
                f"cannot read project backup {source}: {error}") from error
        with archive:
            names = archive.namelist()
            if any(not self._safe_member(name) for name in names):
                raise ProjectAdministrationError(
                    "project backup contains an unsafe path")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, ValueError) as error:
                raise ProjectAdministrationError(
                    "project backup has no valid manifest") from error
            if manifest.get("type") != "azeo.engineering_project_backup":
                raise ProjectAdministrationError(
                    "file is not an Azeo engineering-project backup")
            target_name = _validate_name(
                project_name or manifest.get("project_name") or "")
            target = self._unused_target(target_name)
            staging = self._staging(target.name)
            try:
                hashes = manifest.get("files") or {}
                if not isinstance(hashes, Mapping):
                    raise ProjectAdministrationError(
                        "project backup manifest file index is invalid")
                for relative, expected in hashes.items():
                    relative_path = PurePosixPath(str(relative))
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise ProjectAdministrationError(
                            "project backup manifest contains an unsafe path")
                    member = PurePosixPath("project", *relative_path.parts)
                    data = archive.read(member.as_posix())
                    if hashlib.sha256(data).hexdigest() != expected:
                        raise ProjectAdministrationError(
                            f"project backup checksum failed for {relative}")
                    destination = staging.joinpath(*relative_path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                _read_document(staging / "_project.json")
                os.replace(staging, target)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return target.resolve()

    def migration_preview(self, name: str) -> list[str]:
        document = _read_document(self._project(name) / "_project.json")
        changes = []
        if not document.get("schema_version"):
            changes.append("Set project schema_version to 1")
        if not document.get("project_id"):
            changes.append("Assign a stable project UUID")
        if not document.get("name"):
            changes.append("Record the canonical project name")
        return changes

    def migrate(self, name: str) -> list[str]:
        project = self._project(name)
        path = project / "_project.json"
        document = _read_document(path)
        changes = self.migration_preview(name)
        if not changes:
            return []
        document.setdefault("schema_version", 1)
        document.setdefault("project_id", str(uuid.uuid4()))
        document.setdefault("name", project.name)
        document["migrated_at"] = _utc_now()
        _write_json_atomic(path, document)
        return changes


__all__ = [
    "ProjectAdministrationError", "ProjectAdministrator", "ProjectRecord",
]
