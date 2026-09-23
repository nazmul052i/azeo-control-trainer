"""Validated, transactional bulk engineering for control modules.

Bulk authoring is intentionally a preview/apply workflow.  A CSV typo must not
partially rewrite a project or silently create a block property that runtime
ignores.  The pure preparation functions below build detached documents,
resolve real block schemas, compile every candidate, and return an immutable
operation which can then be committed as one filesystem transaction.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


_MODULE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class GenerationRow:
    module: str
    prefix: str = ""


@dataclass(frozen=True)
class EditRow:
    module: str
    block: str
    parameter: str
    value: str


@dataclass(frozen=True)
class BulkChange:
    module: str
    target: str
    before: Any
    after: Any


@dataclass
class PreparedBulkOperation:
    """Fully validated file replacements, safe to display before applying."""

    documents: dict[Path, dict]
    changes: list[BulkChange]
    create_only: bool = False
    warnings: list[str] = field(default_factory=list)
    _originals: dict[Path, bytes | None] = field(
        default_factory=dict, init=False, repr=False)
    _applied_targets: list[Path] = field(
        default_factory=list, init=False, repr=False)

    def apply(self) -> list[Path]:
        """Commit every document or restore every original byte sequence."""
        if not self.documents:
            return []
        targets = [Path(path) for path in self.documents]
        if self.create_only:
            existing = [path for path in targets if path.exists()]
            if existing:
                raise FileExistsError(
                    "Bulk generation refuses to replace existing modules: "
                    + ", ".join(path.name for path in existing))

        originals: dict[Path, bytes | None] = {}
        staged: dict[Path, Path] = {}
        replaced: list[Path] = []
        try:
            for path, document in self.documents.items():
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[path] = path.read_bytes() if path.exists() else None
                fd, temporary = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".bulk.tmp", dir=path.parent)
                temporary_path = Path(temporary)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(document, stream, indent=2, ensure_ascii=False,
                              default=str)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                staged[path] = temporary_path

            for path in targets:
                os.replace(staged[path], path)
                replaced.append(path)
            self._originals = originals
            self._applied_targets = list(targets)
            return targets
        except BaseException as exc:
            failures = _restore_originals(originals, replaced)
            if failures:
                raise RuntimeError(
                    "Bulk apply failed and rollback was incomplete for: "
                    + ", ".join(path.name for path in failures)) from exc
            raise
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

    def rollback(self) -> list[Path]:
        """Restore the exact files replaced by the last successful apply."""
        if not self._applied_targets:
            return []
        targets = list(self._applied_targets)
        failures = _restore_originals(self._originals, targets)
        if failures:
            raise OSError(
                "Bulk rollback was incomplete for: "
                + ", ".join(path.name for path in failures))
        self._originals = {}
        self._applied_targets = []
        return targets


def _restore_originals(
    originals: Mapping[Path, bytes | None], targets: Iterable[Path],
) -> list[Path]:
    """Restore target bytes atomically and report any unrecovered paths."""
    failures: list[Path] = []
    for path in reversed(list(targets)):
        original = originals.get(path)
        temporary_path: Path | None = None
        try:
            if original is None:
                path.unlink(missing_ok=True)
                continue
            fd, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".rollback.tmp",
                dir=path.parent)
            temporary_path = Path(temporary)
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except OSError:
            failures.append(path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return failures


def parse_generation_csv(text: str) -> list[GenerationRow]:
    """Parse ``module,prefix`` rows with an optional header."""
    rows = _csv_rows(text)
    if rows and rows[0] and rows[0][0].strip().casefold() in {
            "module", "module_name", "name"}:
        rows = rows[1:]
    result: list[GenerationRow] = []
    for number, row in enumerate(rows, start=1):
        if not row or not any(part.strip() for part in row):
            continue
        if len(row) > 2:
            raise ValueError(f"Generation row {number} has more than 2 columns")
        result.append(GenerationRow(
            module=row[0].strip(), prefix=row[1].strip() if len(row) > 1 else ""))
    if not result:
        raise ValueError("Enter at least one module row")
    return result


def parse_edit_csv(text: str) -> list[EditRow]:
    """Parse ``module,block,parameter,value`` rows with an optional header."""
    rows = _csv_rows(text)
    if rows and rows[0] and rows[0][0].strip().casefold() == "module":
        rows = rows[1:]
    result: list[EditRow] = []
    for number, row in enumerate(rows, start=1):
        if not row or not any(part.strip() for part in row):
            continue
        if len(row) != 4:
            raise ValueError(
                f"Edit row {number} needs module, block, parameter, value")
        result.append(EditRow(*(part.strip() for part in row)))
    if not result:
        raise ValueError("Enter at least one edit row")
    return result


def _csv_rows(text: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(str(text)), strict=True))
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}") from exc


def prepare_generation(
    template_document: Mapping[str, Any],
    target_directory: Path | str,
    rows: Iterable[GenerationRow],
) -> PreparedBulkOperation:
    """Create compiled module candidates from an explicit token template.

    Supported tokens in any string value are ``{{MODULE}}`` and
    ``{{PREFIX}}``.  Block identities are deterministically remapped per
    target module so generated documents never share engineering identities.
    """
    target_directory = Path(target_directory).resolve()
    supplied = list(rows)
    names = [row.module for row in supplied]
    _validate_module_names(names)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Generated module names must be unique")
    if not template_document.get("blocks"):
        raise ValueError("The selected template module has no blocks")
    if template_document.get("module_class"):
        raise ValueError(
            "The source is a linked Control Module Class instance. Use "
            "Tools > Classes > Create Linked Instance so class identity, "
            "overrides, and revision tracking remain valid")

    documents: dict[Path, dict] = {}
    changes: list[BulkChange] = []
    warnings: list[str] = []
    for row in supplied:
        safe_file = f"{row.module}.json"
        target = (target_directory / safe_file).resolve()
        try:
            target.relative_to(target_directory)
        except ValueError as exc:  # defensive; name validation should catch it
            raise ValueError(f"Target escapes the module folder: {row.module}") from exc
        if target.exists():
            raise FileExistsError(f"Module already exists: {target.name}")
        document = _substitute(copy.deepcopy(dict(template_document)), {
            "{{MODULE}}": row.module,
            "{{PREFIX}}": row.prefix,
        })
        document["name"] = row.module
        _remap_block_ids(document, row.module)
        unresolved = sorted(_find_unresolved_tokens(document))
        if unresolved:
            raise ValueError(
                f"{row.module}: unresolved template token(s): "
                + ", ".join(unresolved))
        _validate_candidate(document, row.module)
        documents[target] = document
        changes.append(BulkChange(
            row.module, "module", None,
            f"Create from {template_document.get('name', 'template')}"))
        if not row.prefix and "{{PREFIX}}" in json.dumps(template_document):
            warnings.append(f"{row.module}: PREFIX is blank")
    return PreparedBulkOperation(
        documents, changes, create_only=True, warnings=warnings)


def prepare_edits(
    modules: Mapping[Path | str, Mapping[str, Any]],
    rows: Iterable[EditRow],
) -> PreparedBulkOperation:
    """Prepare schema-checked edits against known project modules."""
    source_by_name: dict[str, tuple[Path, dict]] = {}
    for raw_path, raw_document in modules.items():
        path = Path(raw_path).resolve()
        document = copy.deepcopy(dict(raw_document))
        name = str(document.get("name") or path.stem)
        key = name.casefold()
        if key in source_by_name:
            raise ValueError(f"Module name is ambiguous: {name}")
        source_by_name[key] = (path, document)

    changes: list[BulkChange] = []
    touched: set[Path] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    for number, row in enumerate(rows, start=1):
        entry = source_by_name.get(row.module.casefold())
        if entry is None:
            raise ValueError(f"Edit row {number}: unknown module {row.module!r}")
        path, document = entry
        target_key = (row.module.casefold(), row.block.casefold(),
                      row.parameter.casefold())
        if target_key in seen_targets:
            raise ValueError(
                f"Edit row {number}: duplicate target "
                f"{row.module}.{row.block}.{row.parameter}")
        seen_targets.add(target_key)
        before, after, target = _apply_edit(document, row, number)
        if before != after:
            changes.append(BulkChange(row.module, target, before, after))
            touched.add(path)

    documents: dict[Path, dict] = {}
    for path, document in (value for value in source_by_name.values()):
        if path not in touched:
            continue
        _validate_candidate(document, str(document.get("name") or path.stem))
        documents[path] = document
    if not changes:
        raise ValueError("The proposed bulk edit makes no changes")
    return PreparedBulkOperation(documents, changes, create_only=False)


def _validate_module_names(names: Iterable[str]) -> None:
    for name in names:
        if not _MODULE_NAME.fullmatch(str(name)) or name in {".", ".."}:
            raise ValueError(
                f"Invalid module name {name!r}; use letters, numbers, dot, "
                "underscore or hyphen, starting with a letter")


def _substitute(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [_substitute(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, replacements)
                for key, item in value.items()}
    return value


def _find_unresolved_tokens(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", value))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_unresolved_tokens(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_find_unresolved_tokens(item))
    return found


def _remap_block_ids(document: dict, module_name: str) -> None:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"azeo:bulk:{module_name}")
    _remap_graph_ids(document, namespace)


def _remap_graph_ids(document: dict, namespace: uuid.UUID) -> None:
    """Remap one graph and every embedded composite graph in its own scope."""
    id_map: dict[str, str] = {}
    blocks = document.get("blocks", [])
    for block in blocks:
        old = str(block.get("id") or uuid.uuid4().hex[:12])
        new = uuid.uuid5(namespace, old).hex[:12]
        id_map[old] = new
        block["id"] = new
    for wire in document.get("wires", []):
        for key in ("src_block_id", "dst_block_id"):
            if str(wire.get(key)) in id_map:
                wire[key] = id_map[str(wire[key])]
        if "id" in wire:
            wire["id"] = uuid.uuid5(namespace, str(wire["id"])).hex[:12]
    for block in blocks:
        inner = block.get("inner_graph")
        if not isinstance(inner, dict):
            continue
        child_namespace = uuid.uuid5(namespace, f"{block['id']}:inner")
        _remap_graph_ids(inner, child_namespace)


def _apply_edit(document: dict, row: EditRow,
                row_number: int) -> tuple[Any, Any, str]:
    if row.block.casefold() in {"@module", "module"}:
        key = row.parameter.strip()
        expected = {"description": str, "scan_ms": int}.get(key)
        if expected is None:
            raise ValueError(
                f"Edit row {row_number}: module property must be description "
                "or scan_ms")
        before = document.get(key, "" if expected is str else 500)
        after = _coerce(row.value, expected)
        if key == "scan_ms" and int(after) < 10:
            raise ValueError(f"Edit row {row_number}: scan_ms must be at least 10")
        if key == "scan_ms" and int(after) == 500:
            document.pop(key, None)
        else:
            document[key] = after
        return before, after, f"Module.{key}"

    candidates = [block for block in document.get("blocks", [])
                  if str(block.get("id", "")).casefold() == row.block.casefold()
                  or str(block.get("instance_name", "")).casefold()
                  == row.block.casefold()]
    if len(candidates) != 1:
        problem = "unknown" if not candidates else "ambiguous"
        raise ValueError(
            f"Edit row {row_number}: {problem} block {row.block!r} in "
            f"{row.module}")
    block_document = candidates[0]
    block = _materialize_block(block_document)
    schema = block.get_config_schema()
    canonical = next((key for key in schema
                      if key.casefold() == row.parameter.casefold()), None)
    if canonical is None:
        raise ValueError(
            f"Edit row {row_number}: {block.instance_name} has no "
            f"configuration parameter {row.parameter!r}")
    spec = schema[canonical]
    if not isinstance(spec, (tuple, list)) or not spec:
        raise ValueError(
            f"Edit row {row_number}: {canonical} has an invalid schema")
    expected = spec[0]
    default = spec[1] if len(spec) > 1 else None
    config = block_document.setdefault("config", {})
    before = config.get(canonical, default)
    after = _coerce(row.value, expected)
    choices = tuple(getattr(block, "choices_for", lambda _key: ())(
        canonical))
    if choices and str(after) not in choices:
        raise ValueError(
            f"Edit row {row_number}: {after!r} is not one of "
            + ", ".join(map(str, choices)))
    config[canonical] = after
    return before, after, f"{block.instance_name}.{canonical}"


def _materialize_block(block_document: Mapping[str, Any]):
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from .model.block_registry import registry

    block_type = str(block_document.get("block_type") or "")
    block_class = registry.get(block_type)
    if block_class is None:
        raise ValueError(f"Unknown block type {block_type!r}")
    return block_class.from_dict(dict(block_document))


def _coerce(raw: str, expected: type) -> Any:
    if expected is str:
        return str(raw)
    if expected is bool:
        normalized = str(raw).strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"{raw!r} is not a Boolean")
    if expected is int:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{raw!r} is not an integer") from exc
        if not value.is_integer():
            raise ValueError(f"{raw!r} is not an integer")
        return int(value)
    if expected is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"{raw!r} is not a number") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    try:
        return expected(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot convert {raw!r} to {expected.__name__}") from exc


def _validate_candidate(document: dict, module_name: str) -> None:
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from .engine.compiler import CompileError, compile_strategy
    from .serialization.strategy_io import graph_from_document

    try:
        graph, _comments = graph_from_document(document, strict=True)
        compile_strategy(graph)
    except (KeyError, TypeError, ValueError, CompileError) as exc:
        raise ValueError(f"{module_name}: candidate does not compile: {exc}") from exc
