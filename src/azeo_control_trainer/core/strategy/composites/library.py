"""Versioned library definitions for linked composite blocks.

An embedded composite owns its graph.  A linked composite owns a reference to
one of these definitions plus a last-known snapshot and explicit instance
overrides.  The snapshot keeps a downloaded module executable when the
engineering library is unavailable; identity/revision keeps it refreshable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid


SCHEMA_VERSION = 1


class CompositeRevisionConflict(RuntimeError):
    """The definition changed after the caller began editing it."""


@dataclass(frozen=True)
class PublicParameter:
    """One definition property that an instance may override."""

    name: str
    path: str
    data_type: str = "FLOAT"
    default: object = 0.0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "data_type": self.data_type,
            "default": self.default,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PublicParameter":
        return cls(
            name=str(value.get("name", "")),
            path=str(value.get("path", "")),
            data_type=str(value.get("data_type", "FLOAT")).upper(),
            default=value.get("default", 0.0),
            description=str(value.get("description", "")),
        )


@dataclass
class CompositeDefinition:
    """A reusable FBD/SFC structure at one committed revision."""

    id: str
    name: str
    revision: int
    graph: dict
    description: str = ""
    language: str = "FBD"
    public_parameters: list[PublicParameter] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    created_at: str = ""
    updated_at: str = ""
    digest: str = ""

    def normalized(self) -> "CompositeDefinition":
        self.name = self.name.strip()
        self.language = self.language.strip().upper() or "FBD"
        if self.language not in {"FBD", "SFC"}:
            raise ValueError("composite language must be FBD or SFC")
        if not self.name:
            raise ValueError("composite definition needs a name")
        if not isinstance(self.graph, dict):
            raise ValueError("composite definition graph must be an object")
        names: set[str] = set()
        paths: set[str] = set()
        for parameter in self.public_parameters:
            if not parameter.name or not parameter.path:
                raise ValueError("public parameters need a name and path")
            if parameter.name in names:
                raise ValueError(
                    f"duplicate public parameter name {parameter.name!r}")
            if parameter.path in paths:
                raise ValueError(
                    f"duplicate public parameter path {parameter.path!r}")
            names.add(parameter.name)
            paths.add(parameter.path)
        self.digest = self.compute_digest()
        return self

    def compute_digest(self) -> str:
        payload = {
            "graph": self.graph,
            "language": self.language,
            "public_parameters": [p.to_dict() for p in self.public_parameters],
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict:
        self.normalized()
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "language": self.language,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "digest": self.digest,
            "public_parameters": [p.to_dict() for p in self.public_parameters],
            "graph": self.graph,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "CompositeDefinition":
        schema = int(value.get("schema_version", 0))
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported composite schema {schema}; expected {SCHEMA_VERSION}")
        definition = cls(
            id=str(value.get("id", "")),
            name=str(value.get("name", "")),
            description=str(value.get("description", "")),
            language=str(value.get("language", "FBD")),
            revision=int(value.get("revision", 0)),
            graph=dict(value.get("graph", {})),
            public_parameters=[PublicParameter.from_dict(item)
                               for item in value.get("public_parameters", [])],
            schema_version=schema,
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            digest=str(value.get("digest", "")),
        ).normalized()
        recorded = str(value.get("digest", ""))
        if recorded and recorded != definition.digest:
            raise ValueError(
                f"composite {definition.id!r} content digest does not match")
        if not definition.id or definition.revision < 1:
            raise ValueError("composite definition identity/revision is invalid")
        return definition


class CompositeLibrary:
    """Filesystem-backed definitions with optimistic revision checks."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.history_root = self.root / "_history"

    def path_for(self, definition_id: str) -> Path:
        return self.root / f"{definition_id}.json"

    def _history_path(self, definition_id: str, revision: int) -> Path:
        return self.history_root / definition_id / f"r{revision:06d}.json"

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        from ..serialization.strategy_io import write_json_transactional

        write_json_transactional(path, payload)

    def create(self, name: str, graph: dict, *, description: str = "",
               language: str = "FBD",
               public_parameters: list[PublicParameter] | None = None,
               definition_id: str | None = None) -> CompositeDefinition:
        definition_id = definition_id or str(uuid.uuid4())
        path = self.path_for(definition_id)
        if path.exists():
            raise FileExistsError(path)
        if any(item.name.casefold() == name.strip().casefold()
               for item in self.list()):
            raise ValueError(f"a composite named {name!r} already exists")
        now = datetime.now(timezone.utc).isoformat()
        definition = CompositeDefinition(
            id=definition_id,
            name=name,
            description=description,
            language=language,
            revision=1,
            graph=dict(graph),
            public_parameters=list(public_parameters or []),
            created_at=now,
            updated_at=now,
        ).normalized()
        self._write(path, definition.to_dict())
        return definition

    def get(self, definition_id: str) -> CompositeDefinition:
        path = self.path_for(definition_id)
        if not path.exists():
            raise KeyError(definition_id)
        return CompositeDefinition.from_dict(self._read(path))

    def find(self, name: str) -> CompositeDefinition | None:
        target = name.strip().casefold()
        return next((item for item in self.list()
                     if item.name.casefold() == target), None)

    def list(self) -> list[CompositeDefinition]:
        if not self.root.exists():
            return []
        definitions = []
        for path in sorted(self.root.glob("*.json")):
            try:
                definitions.append(
                    CompositeDefinition.from_dict(self._read(path)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # A damaged definition is not silently returned as usable.
                continue
        return sorted(definitions, key=lambda item: item.name.casefold())

    def update(self, definition_id: str, *, expected_revision: int,
               graph: dict | None = None, name: str | None = None,
               description: str | None = None,
               language: str | None = None,
               public_parameters: list[PublicParameter] | None = None,
               note: str = "") -> CompositeDefinition:
        current = self.get(definition_id)
        if current.revision != int(expected_revision):
            raise CompositeRevisionConflict(
                f"{current.name} is revision {current.revision}; "
                f"the editor started from {expected_revision}")
        # Preserve the exact previous record before publishing its successor.
        archived = current.to_dict()
        if note:
            archived["revision_note"] = str(note)
        self._write(
            self._history_path(definition_id, current.revision), archived)
        updated = CompositeDefinition(
            id=current.id,
            name=current.name if name is None else name,
            description=current.description if description is None else description,
            language=current.language if language is None else language,
            revision=current.revision + 1,
            graph=current.graph if graph is None else dict(graph),
            public_parameters=(current.public_parameters
                               if public_parameters is None
                               else list(public_parameters)),
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).normalized()
        duplicate = next((item for item in self.list()
                          if item.id != updated.id and
                          item.name.casefold() == updated.name.casefold()), None)
        if duplicate:
            raise ValueError(
                f"a composite named {updated.name!r} already exists")
        self._write(self.path_for(definition_id), updated.to_dict())
        return updated

    def get_revision(self, definition_id: str,
                     revision: int) -> CompositeDefinition:
        current = self.get(definition_id)
        if current.revision == int(revision):
            return current
        path = self._history_path(definition_id, int(revision))
        if not path.exists():
            raise KeyError((definition_id, revision))
        return CompositeDefinition.from_dict(self._read(path))

    def revisions(self, definition_id: str) -> list[int]:
        current = self.get(definition_id)
        history = self.history_root / definition_id
        values = []
        if history.exists():
            for path in history.glob("r*.json"):
                try:
                    values.append(int(path.stem[1:]))
                except ValueError:
                    continue
        return sorted(set(values + [current.revision]))

    def delete(self, definition_id: str) -> None:
        """Delete the current definition; revision history remains recoverable."""
        current = self.get(definition_id)
        self._write(
            self._history_path(definition_id, current.revision),
            current.to_dict())
        self.path_for(definition_id).unlink()


def project_composite_library() -> CompositeLibrary:
    """The active project's library, resolved dynamically at call time."""
    from ..serialization import strategy_io

    return CompositeLibrary(strategy_io.STRATEGY_DIR / "_composites")
