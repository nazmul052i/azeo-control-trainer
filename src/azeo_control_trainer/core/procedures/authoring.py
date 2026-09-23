"""Offline editing and immutable project revisions of the runner's documents."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import re
from uuid import uuid4

import yaml
from azeo_control_trainer.core.pa_designer.core.tag_references import referenced_tags_in_expression
from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file

from .model import AdvisoryProcedure
from .library import block_for_type


def catalog_resolves(path: str, available_paths: set[str]) -> bool:
    from azeo_control_trainer.core.hmi.pvms.elements import path_field

    if path in available_paths:
        return True
    field = path_field(path)
    base = path[:-len(field)] if field else path
    # These are fields of an existing terminal in LiveGraphSource, not separate
    # entries in TagDatabase. In particular the runner's MODE.ACTUAL must save.
    return bool(field and base in available_paths and len(base.split("/")) == 3
                and base.split("/")[1].upper() != "PARAMETERS"
                and (field not in {".ACTUAL", ".TARGET"} or base.rsplit("/", 1)[-1] == "MODE"))


def library_documents(library: Path):
    """Staged revisions and per-seat evidence must never appear as procedures."""
    for path in sorted(library.rglob("*.yaml")) if library.exists() else ():
        if any(part in {"runtime", "dependencies"} or part.startswith(".") for part in path.relative_to(library).parts):
            continue
        yield path


@dataclass
class ProcedureDraft:
    data: dict
    bindings: dict[str, str]
    source: Path | None = None
    library: Path | None = None

    def use_memory(self, definition):
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTag
        spec = MemoryTag.model_validate(definition)
        variables = self.data.setdefault("variables", [])
        linked = next((v for v in variables if v.get("tag_path") == spec.path), None)
        if linked:
            return linked["name"]
        alias, number = spec.name, 2
        while any(v["name"] == alias for v in variables):
            alias, number = f"{spec.name}_{number}", number + 1
        variables.append(dict(spec.model_dump(), name=alias, tag_path=spec.path))
        return alias

    def bind_parameter(self, entry):
        for name, path in self.bindings.items():
            if path == entry.path:
                return name
        stem = "P." + re.sub(r"[^A-Za-z0-9_]", "_", entry.path)[:100]
        name, suffix = stem, 2
        while name in self.bindings:
            name, suffix = f"{stem}_{suffix}", suffix + 1
        dtype = {"REAL": "float", "FLOAT": "float", "BOOL": "bool", "INT": "int", "STRING": "str"}.get(
            entry.data_type.upper(), entry.data_type.lower())
        self.data.setdefault("tags", []).append({"tag": name, "data_type": dtype if dtype in {"float", "int", "bool", "str"} else "any",
                                                "access": "read", "description": entry.description})
        self.bindings[name] = entry.path
        return name

    @classmethod
    def new(cls):
        return cls({
            "procedure_id": "new_procedure", "name": "New procedure", "mode": "advisory",
            "metadata": {"version": "1.0", "safety_class": "advisory"},
            "tags": [], "variables": [],
            "steps": [
                dict(block_for_type("instruction").instantiate("review"),
                     description="Review the starting condition and confirm you are ready."),
                dict(block_for_type("operator_comment").instantiate("record"),
                     comment_prompt="Record the observed result."),
                dict(block_for_type("complete").instantiate("complete"), description="Procedure complete."),
            ],
        }, {})

    @classmethod
    def load(cls, path: Path, library: Path):
        from azeo_control_trainer.core.pa_designer.connectors.tag_mapping import TagMappingTable

        path, library = path.resolve(), library.resolve()
        if not path.is_relative_to(library):
            raise ValueError("Open a procedure from this project's library")
        data = load_bounded_yaml_file(path, label="Procedure")
        # An unsupported graph cannot be reduced silently to its step list.
        procedure = AdvisoryProcedure.model_validate(data)
        if procedure.mode.value != "advisory" or procedure.trigger.mode != "manual":
            raise ValueError("The editor supports manually started advisory procedures")
        mapping = (path.parent / procedure.connectivity.mapping_path).resolve()
        if not procedure.connectivity.mapping_path or not mapping.is_relative_to(library):
            raise ValueError("The procedure needs a mapping file inside its project library")
        table = TagMappingTable.from_yaml(mapping)
        return cls(data, {row.logical_tag: row.connector_tag for row in table.rows}, path, library)

    def definition(self, available_paths: set[str] | None = None):
        from .composition import build_definition
        definition = build_definition(AdvisoryProcedure.model_validate(self.data), dict(self.bindings), self.source, self.library)
        # Upstream permits undeclared expressions when the entire tag list is empty.
        # The offline author must receive the same missing-input error before saving.
        refs = set()
        for step in definition.procedure.steps:
            if step.tag:
                refs.add(step.tag)
            for expression in (step.condition, step.expression):
                if expression:
                    refs.update(referenced_tags_in_expression(expression))
        missing = refs - set(definition.bindings)
        if missing:
            raise ValueError("Declare and map referenced tags: " + ", ".join(sorted(missing)))
        if available_paths is not None:
            unresolved = {path for path in definition.bindings.values() if not catalog_resolves(path, available_paths)}
            if unresolved:
                raise ValueError("Parameters not found in this project: " + ", ".join(sorted(unresolved)))
        return definition

    def save_revision(self, project: Path, available_paths: set[str] | None = None) -> Path:
        from azeo_control_trainer.core.configuration.package_paths import release_root

        project = project.resolve()
        if release_root(project):
            raise ValueError("Released configuration is immutable. Author in an editable project.")
        self.library = project / "procedures"
        definition = self.definition(available_paths)
        self.validate_memory(project, definition)
        library = project / "procedures"
        library.mkdir(parents=True, exist_ok=True)
        if not library.resolve().is_relative_to(project):
            raise ValueError("The procedure library must remain inside the project")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = library / f"p-{self.data['procedure_id']}-{stamp}-{uuid4().hex[:8]}"
        staging = library / f".pending-{uuid4().hex}"
        data = deepcopy(self.data)
        data.setdefault("connectivity", {})["mapping_path"] = "mapping.yaml"
        metadata = data.setdefault("metadata", {})
        metadata.update(approval_status="draft", approved_by="", approved_at="", reviewed_by="",
                        reviewed_at="", released_by="", released_at="", modified_at=stamp)
        metadata.setdefault("created_at", stamp)
        # A prior approval authenticates prior bytes, never this new draft.
        data["governance_history"] = []
        staging.mkdir()
        try:
            self._copy_references(data, staging, library)
            from .composition import bundle_dependencies
            bundle_dependencies(data, self.source, library, staging)
            (staging / "mapping.yaml").write_text(yaml.safe_dump({"mappings": [
                {"logical_tag": tag, "connector_tag": path} for tag, path in self.bindings.items()
            ]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
            (staging / "procedure.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            # The runner discovers the pair only after both files are complete.
            staging.rename(target)
        finally:
            if staging.exists():
                if not staging.resolve().is_relative_to(library.resolve()) or not staging.name.startswith(".pending-"):
                    raise ValueError("Refusing to clean a staging path outside the procedure library")
                shutil.rmtree(staging)
        self.data = data
        self.source = target / "procedure.yaml"
        return self.source

    def validate_memory(self, project, definition=None):
        from azeo_control_trainer.core.datastore.memory_tags import MemoryTagStore
        definition = definition or self.definition()
        linked = [v for v in definition.procedure.variables if v.tag_path]
        if not linked:
            return
        catalog = {spec.path: spec for spec in MemoryTagStore(project).definitions()}
        for spec in linked:
            current = catalog.get(spec.tag_path)
            if current is None or any(getattr(spec, key) != getattr(current, key)
                                      for key in ("data_type", "min_value", "max_value")):
                raise ValueError(f"Memory tag is missing or its definition changed: {spec.tag_path}")

    def _copy_references(self, data, staging, library):
        paths = [entry["path"] for entry in data.get("references", [])]
        for step in data["steps"]:
            paths.extend(step[key] for key in ("document_link", "video_link") if step.get(key))
        recovery = data.get("recovery", {}).get("recovery_procedure_path")
        if recovery:
            raise ValueError("Linked recovery procedures are not supported by this revision editor yet")
        for value in set(paths):
            if value.startswith(("https://", "http://")):
                continue
            relative = Path(value)
            if self.source is None or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Cannot retain relative reference: {value}")
            source = (self.source.parent / relative).resolve()
            destination = (staging / relative).resolve()
            if (not source.is_relative_to(library.resolve()) or not source.is_file()
                    or not destination.is_relative_to(staging.resolve())
                    or relative.as_posix().casefold() in {"procedure.yaml", "mapping.yaml"}
                    or "runtime" in relative.parts):
                raise ValueError(f"Reference must be a document inside the procedure library: {value}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
