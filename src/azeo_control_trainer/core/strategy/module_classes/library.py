"""Project-local, revisioned definitions for class-based Control Modules.

A module class is not a copy template.  It is the validated source definition
identified by a stable UUID and monotonically increasing revision.  Linked
module instances embed the last adopted definition so controllers remain
independent of this engineering library at runtime.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from ..composites.library import PublicParameter


SCHEMA_VERSION = 1
DEFINITION_KIND = "control_module_class"


class ModuleClassRevisionConflict(RuntimeError):
    """The class changed after the caller began its reviewed edit."""


def _normalized_id(value: str) -> str:
    """Accept only canonical UUID identities before composing file paths."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("module class identity must be a UUID") from exc


@dataclass
class ModuleClassDefinition:
    """One committed revision of a reusable complete Control Module."""

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
    definition_kind: str = DEFINITION_KIND

    def normalized(self) -> "ModuleClassDefinition":
        self.id = _normalized_id(self.id)
        self.name = str(self.name).strip()
        self.description = str(self.description)
        self.language = str(self.language).strip().upper() or "FBD"
        if not self.id:
            raise ValueError("module class needs a stable identity")
        if not self.name:
            raise ValueError("module class needs a name")
        if self.revision < 1:
            raise ValueError("module class revision must be positive")
        if self.language not in {"FBD", "SFC"}:
            raise ValueError("module class language must be FBD or SFC")
        if not isinstance(self.graph, dict):
            raise ValueError("module class graph must be an object")
        self.graph = deepcopy(self.graph)
        self.graph.pop("module_class", None)
        self.graph["name"] = self.name
        if not isinstance(self.graph.get("blocks"), list) \
                or not isinstance(self.graph.get("wires"), list):
            raise ValueError("module class graph needs blocks and wires")
        names: set[str] = set()
        paths: set[str] = set()
        for parameter in self.public_parameters:
            if not parameter.name.strip() or not parameter.path.strip():
                raise ValueError("public parameters need a name and path")
            name = parameter.name.casefold()
            path = parameter.path.replace("\\", "/").casefold()
            if name in names:
                raise ValueError(
                    f"duplicate public parameter name {parameter.name!r}")
            if path in paths:
                raise ValueError(
                    f"duplicate public parameter path {parameter.path!r}")
            names.add(name)
            paths.add(path)
        self.digest = self.compute_digest()
        return self

    def compute_digest(self) -> str:
        payload = {
            "graph": self.graph,
            "language": self.language,
            "public_parameters": [item.to_dict()
                                  for item in self.public_parameters],
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict:
        self.normalized()
        return {
            "schema_version": self.schema_version,
            "definition_kind": DEFINITION_KIND,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "language": self.language,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "digest": self.digest,
            "public_parameters": [item.to_dict()
                                  for item in self.public_parameters],
            "graph": deepcopy(self.graph),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "ModuleClassDefinition":
        if not isinstance(value, dict):
            raise ValueError("module class record must be an object")
        schema = int(value.get("schema_version", 0))
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported module class schema {schema}; "
                f"expected {SCHEMA_VERSION}")
        kind = str(value.get("definition_kind", ""))
        if kind != DEFINITION_KIND:
            raise ValueError(f"record is not a {DEFINITION_KIND}")
        definition = cls(
            id=str(value.get("id", "")),
            name=str(value.get("name", "")),
            description=str(value.get("description", "")),
            language=str(value.get("language", "FBD")),
            revision=int(value.get("revision", 0)),
            graph=deepcopy(value.get("graph", {})),
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
                f"module class {definition.id!r} digest does not match")
        return definition


def validate_definition(definition: ModuleClassDefinition) -> None:
    """Strict-load, apply defaults, and compile a class revision."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    from ..blocks.composite_blocks import (
        _assign_public_parameter,
        _coerce_public_value,
    )
    from ..engine.compiler import compile_strategy
    from ..serialization.strategy_io import graph_from_document

    normalized = ModuleClassDefinition.from_dict(definition.to_dict())
    graph, _comments = graph_from_document(normalized.graph, strict=True)
    for parameter in normalized.public_parameters:
        value = _coerce_public_value(
            parameter.default, parameter.data_type)
        _assign_public_parameter(graph, parameter.path, value)
    compile_strategy(graph)


def _canonical_graph(
    name: str, graph: dict, public_parameters: list[PublicParameter],
) -> dict:
    """Store public paths at their declared defaults, never instance values."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    from ..blocks.composite_blocks import (
        _assign_public_parameter,
        _coerce_public_value,
    )
    from ..serialization.strategy_io import graph_from_document

    document = deepcopy(dict(graph))
    document.pop("module_class", None)
    document["name"] = name
    loaded, comments = graph_from_document(document, strict=True)
    for parameter in public_parameters:
        _assign_public_parameter(
            loaded, parameter.path,
            _coerce_public_value(parameter.default, parameter.data_type))
    result = loaded.to_dict()
    result["name"] = name
    if comments or "comments" in document:
        result["comments"] = deepcopy(comments)
    return result


class ModuleClassLibrary:
    """Filesystem-backed module classes with immutable revision history."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.history_root = self.root / "_history"

    def path_for(self, definition_id: str) -> Path:
        return self.root / f"{_normalized_id(definition_id)}.json"

    def _history_path(self, definition_id: str, revision: int) -> Path:
        return (self.history_root / _normalized_id(definition_id)
                / f"r{revision:06d}.json")

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        from ..serialization.strategy_io import write_json_transactional

        write_json_transactional(path, payload)

    def create(
        self, name: str, graph: dict, *, description: str = "",
        language: str = "FBD",
        public_parameters: list[PublicParameter] | None = None,
        definition_id: str | None = None,
    ) -> ModuleClassDefinition:
        definition_id = definition_id or str(uuid.uuid4())
        if self.path_for(definition_id).exists():
            raise FileExistsError(self.path_for(definition_id))
        if any(item.name.casefold() == name.strip().casefold()
               for item in self.list()):
            raise ValueError(f"a module class named {name!r} already exists")
        parameters = list(public_parameters or [])
        now = datetime.now(timezone.utc).isoformat()
        definition = ModuleClassDefinition(
            id=definition_id,
            name=name,
            description=description,
            language=language,
            revision=1,
            graph=_canonical_graph(name, graph, parameters),
            public_parameters=parameters,
            created_at=now,
            updated_at=now,
        ).normalized()
        validate_definition(definition)
        self._write(self.path_for(definition.id), definition.to_dict())
        return definition

    def get(self, definition_id: str) -> ModuleClassDefinition:
        path = self.path_for(definition_id)
        if not path.exists():
            raise KeyError(definition_id)
        return ModuleClassDefinition.from_dict(self._read(path))

    def find(self, name: str) -> ModuleClassDefinition | None:
        wanted = str(name).strip().casefold()
        return next((item for item in self.list()
                     if item.name.casefold() == wanted), None)

    def list(self) -> list[ModuleClassDefinition]:
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob("*.json")):
            try:
                result.append(ModuleClassDefinition.from_dict(
                    self._read(path)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda item: item.name.casefold())

    def update(
        self, definition_id: str, *, expected_revision: int,
        graph: dict | None = None, name: str | None = None,
        description: str | None = None, language: str | None = None,
        public_parameters: list[PublicParameter] | None = None,
        note: str = "",
    ) -> ModuleClassDefinition:
        current = self.get(definition_id)
        if current.revision != int(expected_revision):
            raise ModuleClassRevisionConflict(
                f"{current.name} is revision {current.revision}; "
                f"the editor started from {expected_revision}")
        archived = current.to_dict()
        if note:
            archived["revision_note"] = str(note)
        self._write(
            self._history_path(current.id, current.revision), archived)
        next_name = current.name if name is None else name
        next_parameters = (current.public_parameters
                           if public_parameters is None
                           else list(public_parameters))
        source_graph = current.graph if graph is None else graph
        updated = ModuleClassDefinition(
            id=current.id,
            name=next_name,
            description=(current.description if description is None
                         else description),
            language=current.language if language is None else language,
            revision=current.revision + 1,
            graph=_canonical_graph(
                next_name, source_graph, next_parameters),
            public_parameters=next_parameters,
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).normalized()
        duplicate = next((item for item in self.list()
                          if item.id != updated.id
                          and item.name.casefold() == updated.name.casefold()),
                         None)
        if duplicate:
            raise ValueError(
                f"a module class named {updated.name!r} already exists")
        validate_definition(updated)
        self._write(self.path_for(updated.id), updated.to_dict())
        return updated

    def get_revision(
        self, definition_id: str, revision: int,
    ) -> ModuleClassDefinition:
        current = self.get(definition_id)
        if current.revision == int(revision):
            return current
        path = self._history_path(definition_id, int(revision))
        if not path.exists():
            raise KeyError((definition_id, revision))
        return ModuleClassDefinition.from_dict(self._read(path))

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
        current = self.get(definition_id)
        self._write(
            self._history_path(definition_id, current.revision),
            current.to_dict())
        self.path_for(definition_id).unlink()


def project_module_class_library() -> ModuleClassLibrary:
    """Resolve the active project at call time; the launcher rebinds it."""
    from ..serialization import strategy_io

    return ModuleClassLibrary(strategy_io.STRATEGY_DIR / "_module_classes")
