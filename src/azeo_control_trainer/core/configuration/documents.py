"""Lossless, bounded project import contract shared by clients and the service."""
from __future__ import annotations

from ..hmi.compatibility import is_display_document_path

import base64
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PROJECT_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000
RUNTIME_NAMES = {".lock", ".recovery.json", "_accepted.json", "_download.log", ".repository-draft.json", ".repository-release.json"}
RUNTIME_DIRS = {"__pycache__", ".git", "logs", ".pytest_cache"}


class ConfigurationError(ValueError):
    """A safe-to-display configuration command failure."""


class Conflict(ConfigurationError):
    """The caller's snapshot is stale; never silently overwrite it."""


class Forbidden(ConfigurationError):
    """The authenticated identity cannot perform the requested operation."""


class Missing(ConfigurationError):
    """The requested resource does not exist in the authorized project."""


def safe_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    if (not value or len(value) > 1000 or "\\" in value or ":" in value
            or value.startswith("/") or "\x00" in value
            or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)
            or PurePosixPath(value).as_posix() != value):
        raise ConfigurationError("Project paths must be relative, normalized file paths")
    for part in parts:
        stem = part.split(".")[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or stem in {
            f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
        } or any(c in part for c in '<>"|?*') or any(ord(c) < 32 for c in part):
            raise ConfigurationError("Project path contains a reserved Windows filename")
    return value


def exclusion(path: str) -> str:
    p = PurePosixPath(path)
    if any(part in RUNTIME_DIRS for part in p.parts) or p.name in RUNTIME_NAMES:
        return "runtime state"
    if p.name.startswith(".lock") or p.name.endswith((".snapshot.json", ".db", ".db-wal",
                                                     ".db-shm", ".sqlite", ".sqlite3",
                                                     ".sqlite-wal", ".sqlite-shm", ".pyc", ".log")):
        return "runtime state or archive"
    return ""


def read_project(root: Path) -> dict:
    """Read a stable set of bytes without saving, normalizing, or following links."""
    root = Path(root).resolve()
    if not (root / "_project.json").is_file():
        raise ConfigurationError("Select an engineering project containing _project.json")
    files, excluded, stamps = [], [], {}
    total = 0
    for path in sorted(root.rglob("*")):
        # A linked directory must not quietly supply configuration from elsewhere.
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ConfigurationError("Project snapshots cannot contain filesystem links")
        if not path.is_file():
            continue
        relative = safe_path(path.relative_to(root).as_posix())
        reason = exclusion(relative)
        if reason:
            excluded.append({"path": relative, "reason": reason})
            continue
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise ConfigurationError(f"File exceeds the 16 MiB import limit: {relative}")
        content = path.read_bytes()
        after = path.stat()
        stamps[path] = (after.st_mtime_ns, after.st_size)
        if (before.st_mtime_ns, before.st_size) != stamps[path]:
            raise Conflict(f"Source changed while reading: {relative}; retry the preview")
        total += len(content)
        if total > MAX_PROJECT_BYTES or len(files) >= MAX_FILES:
            raise ConfigurationError("Project exceeds the snapshot import limit")
        files.append({"path": relative, "content": base64.b64encode(content).decode("ascii")})
    current = {p for p in root.rglob("*") if p.is_file()
               and not exclusion(p.relative_to(root).as_posix())}
    if current != set(stamps) or any(
        (p.stat().st_mtime_ns, p.stat().st_size) != stamp for p, stamp in stamps.items()
    ):
        raise Conflict("Source changed while reading; retry the preview")
    return {"files": files, "excluded": excluded}


@dataclass(frozen=True)
class Document:
    path: str
    kind: str
    name: str
    digest: str
    content: bytes
    payload: dict | list | None


@dataclass
class PreparedImport:
    documents: list[Document]
    tags: list[dict]
    warnings: list[str]
    digest: str
    catalog: dict

    def summary(self) -> dict:
        return {"files": len(self.documents), "tags": len(self.tags),
                "bytes": sum(len(d.content) for d in self.documents),
                "kinds": dict(Counter(d.kind for d in self.documents)),
                "digest": self.digest, "warnings": self.warnings}


def catalog_value(value):
    """Schema defaults may use infinity for an unbounded limit; JSONB cannot.

    Preserve that meaning explicitly in the projection. The authored bytes are
    retained separately and never rewritten to match this transport convention.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return {"$number": "NaN" if math.isnan(value) else (
            "Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, dict):
        return {key: catalog_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [catalog_value(item) for item in value]
    return value


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON property {key!r}")
        result[key] = value
    return result


def _validate_json_strings(value):
    if isinstance(value, str):
        if "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("JSON contains an unsupported Unicode string")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_strings(key)
            _validate_json_strings(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json_strings(item)


def prepare_import(files: list[dict]) -> PreparedImport:
    """Derive catalog entries on the service; client-supplied indexes are never trusted."""
    from ..strategy import blocks  # noqa: F401 — registers installed block schemas
    from ..strategy.serialization.strategy_io import graph_from_document
    from ..strategy.tagdb import TagDatabase

    if not files or len(files) > MAX_FILES:
        raise ConfigurationError("Import needs between 1 and 10,000 files")
    documents, tags, warnings, paths, modules = [], [], [], set(), set()
    graphs = {}
    total = 0
    for item in files:
        path = safe_path(item["path"])
        if path.casefold() in paths or exclusion(path):
            raise ConfigurationError(f"Duplicate path or excluded runtime file: {path}")
        paths.add(path.casefold())
        encoded = item["content"]
        if len(encoded) > (MAX_FILE_BYTES + 2) // 3 * 4:
            raise ConfigurationError(f"File exceeds the 16 MiB import limit: {path}")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ConfigurationError(f"Invalid file encoding: {path}") from error
        total += len(content)
        if total > MAX_PROJECT_BYTES or len(content) > MAX_FILE_BYTES:
            raise ConfigurationError("Project exceeds the snapshot import limit")
        payload, kind, name = None, "asset", PurePosixPath(path).name
        if path.endswith(".json"):
            try:
                payload = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                                     parse_constant=lambda _: (_ for _ in ()).throw(
                                         ValueError("Non-finite JSON number")))
                if not isinstance(payload, (dict, list)):
                    raise ValueError("Expected an object or array")
                # Catch overflowing numeric literals and PostgreSQL's unsupported
                # NUL string value during preview, before any transaction starts.
                json.dumps(payload, allow_nan=False)
                _validate_json_strings(payload)
            except (ValueError, UnicodeError) as error:
                raise ConfigurationError(f"Invalid JSON in {path}: {error}") from error
            kind = "metadata"
        parts = PurePosixPath(path).parts
        if path == "_project.json":
            if not isinstance(payload, dict) or not isinstance(payload.get("areas"), list):
                raise ConfigurationError("_project.json must contain an areas array")
            kind = "project"
        elif "versions" in parts or "revisions" in parts or "_class_revisions" in parts:
            kind = "legacy_revision"
        elif isinstance(payload, dict) and "blocks" in payload and "wires" in payload:
            if parts[0] in {"control", "sequence", "equipment"} or len(parts) == 1:
                kind = "module"
                try:
                    graph, _ = graph_from_document(payload, strict=True)
                    if not graph.name or graph.name.casefold() in modules:
                        raise ValueError("Empty or duplicate module name")
                    modules.add(graph.name.casefold())
                    graphs[path] = graph
                    db = TagDatabase.from_graphs([graph])
                    tags.extend({"source": path, **catalog_value(asdict(tag))} for tag in db)
                except Exception as error:
                    raise ConfigurationError(f"Cannot index module {path}: {error}") from error
        elif (isinstance(payload, dict)
              and payload.get("definition_kind") == "control_module_class"):
            try:
                from ..strategy.module_classes.library import (
                    ModuleClassDefinition,
                    validate_definition,
                )
                definition = ModuleClassDefinition.from_dict(payload)
                validate_definition(definition)
                kind = "module_class"
            except Exception as error:
                raise ConfigurationError(
                    f"Invalid Control Module Class {path}: {error}") from error
        elif is_display_document_path(path):
            kind = "display"
        if isinstance(payload, dict):
            name = str(payload.get("name") or name)
        documents.append(Document(path, kind, name, hashlib.sha256(content).hexdigest(),
                                  content, payload))
    if "_project.json" not in paths:
        raise ConfigurationError("Import must include _project.json")
    from ..hmi.pvms.class_revisions import verify_files
    try:
        verify_files(files)
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigurationError("Invalid pinned class files: " + str(error)) from error
    from .catalog import build_catalog
    catalog = build_catalog(documents, tags, graphs)
    warnings.append(catalog["coverage"])
    warnings.extend(one["message"] for one in catalog["issues"] if one["source"] == "_project.json")
    warnings.append("Files remain authoritative. This snapshot does not report controller or "
                    "station running revisions and cannot be downloaded or published.")
    manifest = [(d.path, d.digest) for d in sorted(documents, key=lambda d: d.path)]
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=True).encode()).hexdigest()
    return PreparedImport(documents, tags, warnings, digest, catalog)


def export_project(bundle: dict, destination: Path) -> Path:
    """Write only to a new directory; verify the manifest before creating anything."""
    import shutil
    import tempfile

    prepared = prepare_import(bundle["files"])
    if prepared.digest != bundle["digest"]:
        raise ConfigurationError("Snapshot manifest verification failed")
    destination = Path(destination).absolute()
    if destination.exists():
        raise ConfigurationError("Export requires a new destination directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".configuration-export-", dir=destination.parent))
    expected_parent = destination.parent.resolve()
    try:
        for document in prepared.documents:
            path = stage / document.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(document.content)
        # rename refuses a concurrent destination on Windows; no replacement of a project.
        if stage.resolve().parent != expected_parent or destination.resolve().parent != expected_parent:
            raise ConfigurationError("Export staging escaped the selected destination directory")
        stage.rename(destination)
    finally:
        if stage.exists():
            if stage.resolve().parent != expected_parent:
                raise ConfigurationError("Export cleanup path is outside the selected destination directory")
            shutil.rmtree(stage)
    return destination
