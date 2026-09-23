"""Engineering-artifact catalogue behind the Library Explorer.

The palette answers "what can I place?" while the Library Explorer answers
"what does this project own, is it healthy, and where is it used?"  Mixing
those questions hid Studio-authored faceplates outside the project tree.  This
module derives a read-only catalogue from the one user-class store, PVM
configuration documents and display drafts; the tree is a view of these
facts, never a second library database.
"""
from __future__ import annotations

from ..compatibility import configuration_document_path, normalize_display_document

from dataclasses import dataclass
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


PVM_CLASS = "pvm"
FACEPLATE_CLASS = "faceplate"
DETAIL_CLASS = "detail"

READY = "Ready"
REVIEW = "Review"
ERROR = "Error"


@dataclass(frozen=True)
class ArtifactRecord:
    """One authored engineering class and its derived project state."""

    name: str
    kind: str
    folder: str
    status: str
    issues: tuple[str, ...]
    used_by: tuple[str, ...]
    paired_with: tuple[str, ...]
    item_count: int
    width: float
    height: float
    configured: bool
    read_only: bool = False

    @property
    def usage_count(self) -> int:
        return len(self.used_by)

    @property
    def kind_title(self) -> str:
        return {
            PVM_CLASS: "PVM Class",
            FACEPLATE_CLASS: "Faceplate Class",
            DETAIL_CLASS: "Detail Display Class",
        }.get(self.kind, self.kind.title())


class LibraryCatalog:
    """Derive class health and cross-references from a graphics library."""

    def __init__(self, root):
        self.root = Path(root)

    def records(self) -> tuple[ArtifactRecord, ...]:
        from .configurator.model import PvmConfiguration
        from .user_library import UserPvmLibrary

        library = UserPvmLibrary(self.root)
        entries = library.entries
        usages = self._usages(entries)
        reverse_pairs: dict[str, list[str]] = {}
        for pvm_name, entry in entries.items():
            pair = str(entry.get("paired_faceplate", "") or "")
            if pair:
                reverse_pairs.setdefault(pair, []).append(pvm_name)

        records = []
        for name, entry in sorted(entries.items()):
            kind = str(entry.get("definition_kind", PVM_CLASS) or PVM_CLASS)
            issues: list[str] = []
            items = list(entry.get("items", ()))
            if not items:
                issues.append("Class contains no drawing elements")
            pair = str(entry.get("paired_faceplate", "") or "")
            paired = ([pair] if pair else []) + reverse_pairs.get(name, [])
            if pair:
                target = entries.get(pair)
                if target is None:
                    issues.append(f"Paired faceplate {pair!r} does not exist")
                elif target.get("definition_kind", PVM_CLASS) \
                        != FACEPLATE_CLASS:
                    issues.append(f"Paired class {pair!r} is not a faceplate")

            config_path = configuration_document_path(self.root, name)
            configured = config_path.exists()
            config_issues = []
            if configured:
                try:
                    config_issues = [str(issue) for issue in
                                     PvmConfiguration.load(
                                         config_path).issues()]
                except Exception as exc:               # noqa: BLE001
                    config_issues = [f"Configuration cannot be read: {exc}"]
            issues.extend(config_issues)

            if issues:
                status = ERROR
            elif not configured:
                status = REVIEW
                issues.append("Typed configuration contract is not defined")
            elif kind == FACEPLATE_CLASS and not reverse_pairs.get(name):
                status = REVIEW
                issues.append("No compact PVM is paired with this faceplate")
            else:
                status = READY

            records.append(ArtifactRecord(
                name=name,
                kind=kind,
                folder=library.folder_title(str(entry.get("folder", "Project") or "Project")),
                status=status,
                issues=tuple(issues),
                used_by=tuple(sorted(usages.get(name, ()))),
                paired_with=tuple(dict.fromkeys(paired)),
                item_count=len(items),
                width=float(entry.get("w", 0.0) or 0.0),
                height=float(entry.get("h", 0.0) or 0.0),
                configured=configured,
            ))
        return tuple(records)

    def record(self, name: str) -> ArtifactRecord | None:
        return next((record for record in self.records()
                     if record.name == name), None)

    def _usages(self, entries: dict) -> dict[str, set[str]]:
        used: dict[str, set[str]] = {name: set() for name in entries}

        # Nested classes and pair relationships are dependencies even before
        # either class is placed on a process display.
        for parent_name, entry in entries.items():
            pair = str(entry.get("paired_faceplate", "") or "")
            if pair in used:
                used[pair].add(f"Paired from {parent_name}")
            for item in entry.get("items", ()):
                child = str(item.get("class", "") or "") \
                    if item.get("kind") == "nested_pvm" else ""
                if child in used:
                    used[child].add(f"Nested in {parent_name}")

        if not self.root.exists():
            return used
        for directory in self.root.iterdir():
            draft = directory / "draft.json"
            if directory.name.startswith("_") or not draft.exists():
                continue
            try:
                document = normalize_display_document(
                    json.loads(draft.read_text(encoding="utf-8")))
            except Exception:                           # noqa: BLE001
                log.warning("Ignoring unreadable display draft %s", draft,
                            exc_info=True)
                continue
            # A class instance expands to several items. Count its display
            # once, not once per shape; the operator cares where, not how many
            # rectangles its implementation uses.
            present = {
                str(item.get("user_pvm", ""))
                for item in document.get("items", ())
                if str(item.get("user_pvm", "")) in used
            }
            for name in present:
                used[name].add(directory.name)
        return used


__all__ = [
    "ArtifactRecord",
    "DETAIL_CLASS",
    "ERROR",
    "FACEPLATE_CLASS",
    "PVM_CLASS",
    "LibraryCatalog",
    "READY",
    "REVIEW",
]
