"""Project-wide graphics configuration services.

Azeo treats displays, layouts, display sets, libraries, and reusable
classes as one transferable configuration.  This module deliberately
operates on those existing documents; it does not introduce another
display format or runtime.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .complexity import measure_display
from .publishing import DisplayStore, PvmDisplay

PACKAGE_FORMAT = "azeo.graphics-configuration/v1"


class InstalledItemError(RuntimeError):
    """A vendor-installed item was edited instead of copied."""


class InstalledItems:
    """Protection flags for items supplied by a graphics library.

    The sidecar is intentionally separate from display documents.  A
    protection bit is repository ownership metadata, not something an
    operator display should carry online.
    """

    def __init__(self, root):
        self.path = Path(root) / "_installed_items.json"

    def _read(self) -> dict[str, list[str]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return {}
        return {str(kind): [str(name) for name in names]
                for kind, names in data.items()
                if isinstance(names, list)} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    def names(self, kind: str) -> tuple[str, ...]:
        return tuple(sorted(self._read().get(kind, ())))

    def is_installed(self, kind: str, name: str) -> bool:
        return str(name) in self._read().get(str(kind), ())

    def protect(self, kind: str, name: str) -> None:
        data = self._read()
        names = set(data.get(str(kind), ()))
        names.add(str(name))
        data[str(kind)] = sorted(names)
        self._write(data)

    def assert_editable(self, kind: str, name: str) -> None:
        if self.is_installed(kind, name):
            raise InstalledItemError(
                f"{kind} {name!r} is installed; copy and rename it before "
                "modifying it")


@dataclass(frozen=True)
class GraphicsPackage:
    """Portable payload containing the one shipping document family."""

    name: str
    displays: dict[str, dict] = field(default_factory=dict)
    layouts: list[dict] = field(default_factory=list)
    display_sets: list[dict] = field(default_factory=list)
    workstations: list[dict] = field(default_factory=list)
    library_files: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "format": PACKAGE_FORMAT,
            "library": self.name,
            "displays": copy.deepcopy(self.displays),
            "layouts": copy.deepcopy(self.layouts),
            "display_sets": copy.deepcopy(self.display_sets),
            "workstations": copy.deepcopy(self.workstations),
            "library_files": copy.deepcopy(self.library_files),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphicsPackage":
        if data.get("format") != PACKAGE_FORMAT:
            raise ValueError("not an Azeo graphics configuration package")
        return cls(
            name=str(data.get("library", "Imported")),
            displays=dict(data.get("displays", {})),
            layouts=list(data.get("layouts", ())),
            display_sets=list(data.get("display_sets", ())),
            workstations=list(data.get("workstations", ())),
            library_files=dict(data.get("library_files", {})))


class ConfigurationLibraryStore:
    """Named configuration libraries plus import/export between roots.

    The active project remains the established display root.  Additional
    libraries live under ``_libraries/<name>`` and contain the exact same
    PvmDisplay/LayoutStore files, so switching never changes formats.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "_libraries.json"

    def _manifest(self) -> dict:
        if not self.path.exists():
            return {"active": "Project", "libraries": ["Project"]}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return {"active": "Project", "libraries": ["Project"]}
        names = list(dict.fromkeys(
            ["Project", *map(str, data.get("libraries", ())) ]))
        active = str(data.get("active", "Project"))
        return {"active": active if active in names else "Project",
                "libraries": names}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    def names(self) -> tuple[str, ...]:
        return tuple(self._manifest()["libraries"])

    @property
    def active(self) -> str:
        return self._manifest()["active"]

    def library_root(self, name: str) -> Path:
        return self.root if name == "Project" else self.root / "_libraries" / name

    def create(self, name: str) -> Path | None:
        name = str(name or "").strip()
        data = self._manifest()
        if not name or name in data["libraries"]:
            return None
        data["libraries"].append(name)
        target = self.library_root(name)
        target.mkdir(parents=True, exist_ok=True)
        self._save(data)
        return target

    def select(self, name: str) -> Path:
        data = self._manifest()
        if name not in data["libraries"]:
            raise KeyError(name)
        data["active"] = name
        self._save(data)
        return self.library_root(name)

    @staticmethod
    def _json_file(path: Path, fallback):
        if not path.exists():
            return copy.deepcopy(fallback)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return copy.deepcopy(fallback)

    def export_package(self, path, *, name: str | None = None) -> GraphicsPackage:
        library = name or self.active
        root = self.library_root(library)
        display_store = DisplayStore(root)
        displays = {}
        for child in sorted(root.iterdir()) if root.exists() else ():
            if child.is_dir():
                draft = display_store.load_draft(child.name)
                if draft is not None:
                    displays[child.name] = draft.to_dict()
        library_files = {}
        for rel in ("_library/user_pvms.json", "_standards.json",
                    "_functions.json", "_templates.json"):
            value = self._json_file(root / rel, None)
            if value is not None:
                library_files[rel] = value
        package = GraphicsPackage(
            library, displays,
            list(self._json_file(root / "_layouts.json", [])),
            list(self._json_file(root / "_display_sets.json", [])),
            list(self._json_file(root / "_workstations.json", [])),
            library_files)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(package.to_dict(), indent=2,
                       ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
        return package

    def import_package(self, path, *, library_name: str = "",
                       overwrite: bool = False) -> tuple[str, ...]:
        package = GraphicsPackage.from_dict(json.loads(
            Path(path).read_text(encoding="utf-8")))
        name = library_name.strip() or package.name
        if name != "Project" and name not in self.names():
            self.create(name)
        root = self.library_root(name)
        installed = InstalledItems(root)
        written = []
        display_store = DisplayStore(root)
        for display_name, document in package.displays.items():
            existing = display_store.load_draft(display_name)
            if existing is not None and not overwrite:
                continue
            if existing is not None:
                installed.assert_editable("display", display_name)
            display_store.save_draft(PvmDisplay.from_dict(document))
            written.append(f"display:{display_name}")
        for filename, rows in (("_layouts.json", package.layouts),
                               ("_display_sets.json", package.display_sets),
                               ("_workstations.json", package.workstations)):
            target = root / filename
            if target.exists() and not overwrite:
                continue
            if target.exists() and filename in (
                    "_layouts.json", "_display_sets.json"):
                kind = "layout" if filename == "_layouts.json" \
                    else "display_set"
                for protected in installed.names(kind):
                    installed.assert_editable(kind, protected)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(rows, indent=2,
                                         ensure_ascii=False) + "\n",
                              encoding="utf-8", newline="\n")
            written.append(filename)
        for rel, data in package.library_files.items():
            target = root / rel
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, indent=2,
                                         ensure_ascii=False) + "\n",
                              encoding="utf-8", newline="\n")
            written.append(rel)
        return tuple(written)


@dataclass(frozen=True)
class Replacement:
    document: str
    occurrences: int


def _replace_value(value, find: str, replace: str):
    if isinstance(value, str):
        count = value.count(find)
        return value.replace(find, replace), count
    if isinstance(value, list):
        total, output = 0, []
        for item in value:
            changed, count = _replace_value(item, find, replace)
            output.append(changed)
            total += count
        return output, total
    if isinstance(value, dict):
        total, output = 0, {}
        for key, item in value.items():
            changed, count = _replace_value(item, find, replace)
            output[key] = changed
            total += count
        return output, total
    return value, 0


def find_replace(root, find: str, replace: str = "", *, apply=False,
                 drafts: dict[str, dict] | None = None,
                 owners: dict[str, DisplayStore] | None = None) \
        -> tuple[Replacement, ...]:
    """Replace configuration, using open drafts in preference to disk copies.

    Open drafts are updated in the supplied map for their editor to checkpoint
    and load. Closed drafts are written under temporary edit locks. Preflight
    every affected display before writing any file, so a locked document
    cannot leave the rest of a project partially replaced.
    """
    if not find:
        return ()
    root = Path(root)
    drafts = drafts if drafts is not None else {}
    owners = owners or {}
    candidates = list(dict.fromkeys([
                  *root.glob("*/draft.json"),
                  *(root / name / "draft.json" for name in drafts),
                  root / "_layouts.json", root / "_display_sets.json",
                  root / "_workstations.json",
                  root / "_standards.json", root / "_functions.json",
                  root / "_templates.json", *root.glob("_library/*.json")]))
    installed = InstalledItems(root)
    results = []
    changes = []
    fingerprints = {}
    for path in candidates:
        name = path.parent.name if path.name == "draft.json" else None
        if name in drafts:
            source = drafts[name]
        else:
            if not path.exists():
                continue
            try:
                raw = path.read_bytes()
                source = json.loads(raw.decode("utf-8"))
                if name is not None:
                    fingerprints[name] = hashlib.sha256(raw).hexdigest()
            except (OSError, ValueError):
                continue
        # Display identity belongs to Rename, not a substring replacement.
        identity = source.get("display", name) if name is not None else None
        if name is not None:
            source = {key: value for key, value in source.items()
                      if key != "display"}
        changed, count = _replace_value(source, find, replace)
        if name is not None:
            changed["display"] = identity
        if not count:
            continue
        label = str(path.relative_to(root)).replace("\\", "/")
        results.append(Replacement(label, count))
        changes.append((path, name, changed))
    if apply:
        from .json_io import atomic_write_json
        from .publishing import DisplayLocked
        temporary = DisplayStore(root)
        acquired = []
        try:
            for _path, name, _changed in changes:
                if name is None:
                    continue
                installed.assert_editable("display", name)
                if name in drafts:
                    if name not in owners or not owners[name].owns_lock(name):
                        raise DisplayLocked(f"{name} is not open for editing.")
                else:
                    temporary.acquire_lock(name)
                    acquired.append(name)
                    if temporary.draft_fingerprint(name) != fingerprints[name]:
                        raise DisplayLocked(f"{name} changed during replacement. Try again.")
            for path, name, changed in changes:
                if name not in drafts:
                    atomic_write_json(path, changed)
            # Mutate caller snapshots only after disk writes succeed.
            for _path, name, changed in changes:
                if name in drafts:
                    drafts[name] = changed
        finally:
            for name in acquired:
                temporary.release_lock(name)
    return tuple(results)


def complexity_report(root, renderer=None) -> tuple[dict, ...]:
    """Measured report across every draft display in one library."""
    root = Path(root)
    store = DisplayStore(root)
    rows = []
    for child in sorted(root.iterdir()) if root.exists() else ():
        if not child.is_dir():
            continue
        display = store.load_draft(child.name)
        if display is None:
            continue
        value = measure_display(display, renderer)
        rows.append({"display": display.name, **value.to_dict()})
    return tuple(rows)
